#include "voice/VoiceRecorder.h"

#include <Arduino.h>
#include <esp_log.h>

#include "board/BoardStorage.h"
#include "voice/Clock.h"
#include "voice/VoiceCapture.h"
#include "voice/VoiceQueue.h"

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        // Bytes, not words.
        constexpr uint32_t kStackBytes = 8192;
        // The same priority as the Arduino loop task, deliberately. At priority 2 this
        // task preempted the UI outright and the recording screen fell to about one
        // frame a second: it woke on every I2S block and wrote to the card while the
        // UI waited. Round-robin at the same priority lets both run, and the I2S DMA
        // ring absorbs the time the UI spends drawing.
        constexpr UBaseType_t kPriority = 1;

    } // namespace

    bool Recorder::start(const NoteMeta& anchor) {
        if (active_) {
            return false;
        }

        auto& fs = Board::Storage::filesystem();
        if (!queue::ensureDir(fs)) {
            error_ = "cartao indisponivel";
            return false;
        }

        anchor_ = anchor;
        anchor_.recordedAt = nowIso8601();
        anchor_.clockSynced = clockSynced();
        path_ = std::string(kQueueDir) + "/" + recordingName(compactStamp(millis()));

        stopRequested_ = false;
        finished_ = false;
        elapsedMs_ = 0;
        level_ = 0;
        error_ = nullptr;
        active_ = true;

        if (xTaskCreate(&Recorder::taskEntry, "voice-rec", kStackBytes, this, kPriority, nullptr) != pdPASS) {
            active_ = false;
            error_ = "sem memoria";
            ESP_LOGE(kTag, "could not start the recording task");
            return false;
        }
        return true;
    }

    void Recorder::requestStop() {
        stopRequested_ = true;
    }

    bool Recorder::active() const {
        return active_;
    }

    uint32_t Recorder::elapsedMs() const {
        return elapsedMs_;
    }

    uint8_t Recorder::level() const {
        return level_;
    }

    const char* Recorder::error() const {
        return error_;
    }

    bool Recorder::takeFinished() {
        if (!finished_) {
            return false;
        }
        finished_ = false;
        return true;
    }

    uint32_t Recorder::lastDurationMs() const {
        return lastDurationMs_;
    }

    CaptureStop Recorder::stopReason() const {
        return stopReason_;
    }

    void Recorder::taskEntry(void* self) {
        static_cast<Recorder*>(self)->run();
    }

    void Recorder::run() {
        CaptureCallbacks callbacks;
        callbacks.onBlock = [this](uint32_t elapsedMs, uint8_t level) {
            elapsedMs_ = elapsedMs;
            level_ = level;
            return !stopRequested_;
        };

        // Zero means unlimited: the note ends when the user says so.
        const auto result = captureToFile(path_.c_str(), 0, kMicGain, 0, &callbacks);

        auto& fs = Board::Storage::filesystem();
        if (result.framesWritten > 0) {
            // The sidecar is written even for a capture that ended in error, because
            // the audio that did land is still a note worth having.
            anchor_.durationMs = result.durationMs;
            queue::writeSidecar(fs, path_.c_str(), anchor_);
        } else {
            // Nothing was recorded. An empty WAV in the queue would be uploaded,
            // transcribed to nothing and clutter the vault.
            fs.remove(path_.c_str());
        }

        lastDurationMs_ = result.durationMs;
        stopReason_ = result.stopReason;
        error_ = result.ok ? nullptr : result.error;
        level_ = 0;
        finished_ = true;
        active_ = false;

        ESP_LOGI(kTag, "recording finished: %u ms, error=%s", static_cast<unsigned>(result.durationMs),
                 error_ != nullptr ? error_ : "none");
        vTaskDelete(nullptr);
    }

} // namespace voice
