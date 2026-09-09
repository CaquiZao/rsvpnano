#include "voice/VoicePlayer.h"

#include <Arduino.h>
#include <FS.h>
#include <esp_log.h>

#include <array>

#include "board/BoardAudio.h"
#include "board/BoardStorage.h"

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        constexpr uint32_t kStackWords = 6144;
        // Same priority as the UI, for the same reason the recorder is: preempting the
        // draw loop outright is what made the recording screen look frozen.
        constexpr UBaseType_t kPriority = 1;
        // The canonical PCM header WavWriter emits. Anything else on the card was not
        // written by this firmware.
        constexpr size_t kHeaderBytes = 44;
        constexpr size_t kFramesPerChunk = 512;
        constexpr uint32_t kSampleRateHz = 16000;

    } // namespace

    bool Player::play(const std::string& wavPath) {
        if (active_) {
            return false;
        }
        path_ = wavPath;
        stopRequested_ = false;
        elapsedMs_ = 0;
        error_ = nullptr;
        active_ = true;

        if (xTaskCreate(&Player::taskEntry, "voice-play", kStackWords, this, kPriority, nullptr) != pdPASS) {
            active_ = false;
            error_ = "sem memoria";
            return false;
        }
        return true;
    }

    void Player::requestStop() {
        stopRequested_ = true;
    }

    bool Player::active() const {
        return active_;
    }

    uint32_t Player::elapsedMs() const {
        return elapsedMs_;
    }

    const char* Player::error() const {
        return error_;
    }

    void Player::taskEntry(void* self) {
        static_cast<Player*>(self)->run();
    }

    void Player::run() {
        File file = Board::Storage::filesystem().open(path_.c_str());
        if (!file) {
            error_ = "nao abriu o arquivo";
        } else if (file.size() <= kHeaderBytes) {
            error_ = "gravacao vazia";
            file.close();
        } else {
            file.seek(kHeaderBytes);

            // The recording is mono; the I2S slot layout is stereo, so each sample is
            // duplicated into both channels on the way out.
            std::array<int16_t, kFramesPerChunk> mono{};
            std::array<int16_t, kFramesPerChunk * 2U> stereo{};
            uint32_t framesPlayed = 0;

            while (!stopRequested_) {
                const size_t read = file.read(reinterpret_cast<uint8_t*>(mono.data()), mono.size() * sizeof(int16_t));
                const size_t frames = read / sizeof(int16_t);
                if (frames == 0) {
                    break;
                }
                for (size_t index = 0; index < frames; ++index) {
                    stereo[index * 2U] = mono[index];
                    stereo[index * 2U + 1U] = mono[index];
                }
                if (!Board::Audio::writeSamples(stereo.data(), frames * 2U, 1000)) {
                    error_ = "falha na reproducao";
                    break;
                }
                framesPlayed += frames;
                elapsedMs_ = (framesPlayed * 1000U) / kSampleRateHz;
            }
            file.close();
            ESP_LOGI(kTag, "played %u ms of %s", static_cast<unsigned>(elapsedMs_), path_.c_str());
        }

        active_ = false;
        vTaskDelete(nullptr);
    }

} // namespace voice
