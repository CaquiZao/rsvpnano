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

        int16_t* allocateBlock() {
            void* buffer = heap_caps_malloc(kBlockSamples * sizeof(int16_t), MALLOC_CAP_SPIRAM);
            if (buffer == nullptr) {
                buffer = heap_caps_malloc(kBlockSamples * sizeof(int16_t), MALLOC_CAP_DEFAULT);
            }
            return static_cast<int16_t*>(buffer);
        }

    } // namespace

    CaptureResult captureToFile(const char* path, uint32_t maxDurationMs) {
        CaptureResult result;

        if (!Board::Audio::prepareInput()) {
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

        const uint32_t startedAt = millis();
        const char* failure = nullptr;
        while ((millis() - startedAt) < maxDurationMs) {
            const size_t read = Board::Audio::readSamples(block, kBlockSamples, 1000);
            if (read == 0) {
                failure = "microphone read returned nothing";
                break;
            }
            if (auto written = writer.writeSamples(block, read); !written) {
                failure = "SD write failed";
                break;
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
        ESP_LOGI(kTag, "captured %u ms to %s", static_cast<unsigned>(result.durationMs), path);
        return result;
    }

} // namespace voice
