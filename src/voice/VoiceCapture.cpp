#include "voice/VoiceCapture.h"

#include <Arduino.h>
#include <FS.h>
#include <esp_heap_caps.h>
#include <esp_log.h>

#include "board/BoardAudio.h"
#include "board/BoardStorage.h"
#include "voice/WavWriter.h"

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        // 16 kHz mono 16-bit is 32 KB/s, so this block holds roughly half a second of
        // audio: enough slack for an SD write to finish while the next block fills.
        constexpr size_t kBlockSamples = 8192;
        // Speech peaks well below full scale, so measuring the meter against a lower
        // reference keeps it lively instead of pinned near the floor. -30 dBFS.
        constexpr uint32_t kLevelReferenceRms = 1036;

        int16_t* allocateBlock() {
            void* buffer = heap_caps_malloc(kBlockSamples * sizeof(int16_t), MALLOC_CAP_SPIRAM);
            if (buffer == nullptr) {
                buffer = heap_caps_malloc(kBlockSamples * sizeof(int16_t), MALLOC_CAP_DEFAULT);
            }
            return static_cast<int16_t*>(buffer);
        }

    } // namespace

    uint8_t blockLevel(const int16_t* samples, size_t sampleCount) {
        if (samples == nullptr || sampleCount == 0) {
            return 0;
        }
        // Sum of squares in 64 bits and one square root per block: a per-sample sqrt
        // would cost more than the SD write it runs alongside.
        uint64_t sumSquares = 0;
        for (size_t index = 0; index < sampleCount; ++index) {
            const int32_t sample = samples[index];
            sumSquares += static_cast<uint64_t>(sample * sample);
        }
        const auto meanSquare = static_cast<double>(sumSquares / sampleCount);
        const auto rms = static_cast<uint32_t>(sqrt(meanSquare));
        const uint32_t scaled = (rms * 255U) / kLevelReferenceRms;
        return static_cast<uint8_t>(scaled > 255U ? 255U : scaled);
    }

    CaptureResult captureToFile(const char* path, uint32_t maxDurationMs, uint8_t micGain, uint8_t micPair,
                                const CaptureCallbacks* callbacks) {
        CaptureResult result;

        if (!Board::Audio::prepareInput(micGain, micPair)) {
            result.error = "microphone unavailable";
            return result;
        }

        int16_t* block = allocateBlock();
        if (block == nullptr) {
            result.error = "out of memory";
            return result;
        }

        File file = Board::Storage::filesystem().open(path, FILE_WRITE);
        if (!file) {
            heap_caps_free(block);
            result.error = "could not open file";
            return result;
        }

        WavWriter writer(file);
        if (auto begun = writer.begin(); !begun) {
            file.close();
            heap_caps_free(block);
            result.error = "could not write header";
            return result;
        }

        // Zero means unlimited, bounded only by the safety ceiling.
        const uint32_t limitMs = maxDurationMs == 0 ? kCaptureCeilingMs : maxDurationMs;
        const uint32_t startedAt = millis();
        const char* failure = nullptr;
        bool stoppedByCaller = false;

        while ((millis() - startedAt) < limitMs) {
            const size_t read = Board::Audio::readSamples(block, kBlockSamples, 1000);
            if (read == 0) {
                failure = "microphone read returned nothing";
                break;
            }
            if (auto written = writer.writeSamples(block, read); !written) {
                failure = "SD write failed";
                break;
            }
            if (callbacks != nullptr && callbacks->onBlock) {
                if (!callbacks->onBlock(millis() - startedAt, blockLevel(block, read))) {
                    stoppedByCaller = true;
                    break;
                }
            }
        }

        // finish() runs even after a failure: a partial recording with a correct header
        // is far more useful than a file the bridge has to repair.
        const auto finished = writer.finish();
        file.close();
        heap_caps_free(block);

        result.framesWritten = writer.frameCount();
        result.durationMs = writer.durationMs();

        if (failure != nullptr) {
            result.error = failure;
            ESP_LOGW(kTag, "capture ended early: %s (%u ms kept)", failure,
                     static_cast<unsigned>(result.durationMs));
            return result;
        }
        if (!finished) {
            result.error = "could not patch header";
            return result;
        }
        result.ok = true;
        ESP_LOGI(kTag, "captured %u ms to %s%s", static_cast<unsigned>(result.durationMs), path,
                 stoppedByCaller ? " (stopped)" : "");
        return result;
    }

} // namespace voice
