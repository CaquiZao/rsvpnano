#pragma once

#include <FS.h>

#include <cstddef>
#include <cstdint>
#include <expected>
#include <system_error>

#include "storage/fs/BufferedWriter.h"

namespace voice {

    // Writes a canonical 44-byte-header PCM WAV. Deliberately independent of ESP_I2S's
    // wav_header.h so this stays compilable and testable in the native test env.
    class WavWriter {
    public:
        static constexpr uint32_t kSampleRateHz = 16000;
        static constexpr uint16_t kChannels = 1;
        static constexpr uint16_t kBitsPerSample = 16;
        static constexpr size_t kHeaderBytes = 44;

        explicit WavWriter(File& file);

        // Reserves the header with zeroed sizes so samples can stream straight after.
        std::expected<void, std::error_code> begin();
        std::expected<void, std::error_code> writeSamples(const int16_t* samples, size_t count);
        // Flushes, seeks back and patches the RIFF and data sizes.
        std::expected<void, std::error_code> finish();

        [[nodiscard]] uint32_t frameCount() const {
            return frames_;
        }
        [[nodiscard]] uint32_t durationMs() const {
            return static_cast<uint32_t>((static_cast<uint64_t>(frames_) * 1000U) / kSampleRateHz);
        }

    private:
        File& file_;
        BufferedWriter writer_;
        uint32_t frames_ = 0;
        bool started_ = false;
        bool finished_ = false;
    };

} // namespace voice
