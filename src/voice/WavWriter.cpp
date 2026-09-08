#include "voice/WavWriter.h"

#include <array>
#include <cstring>

namespace voice {

    namespace {

        constexpr uint32_t kByteRate =
            WavWriter::kSampleRateHz * WavWriter::kChannels * (WavWriter::kBitsPerSample / 8U);
        constexpr uint16_t kBlockAlign = WavWriter::kChannels * (WavWriter::kBitsPerSample / 8U);
        constexpr uint32_t kRiffSizeOffset = 4;
        constexpr uint32_t kDataSizeOffset = 40;

        void put32(uint8_t* at, uint32_t value) {
            at[0] = static_cast<uint8_t>(value & 0xFFU);
            at[1] = static_cast<uint8_t>((value >> 8) & 0xFFU);
            at[2] = static_cast<uint8_t>((value >> 16) & 0xFFU);
            at[3] = static_cast<uint8_t>((value >> 24) & 0xFFU);
        }

        void put16(uint8_t* at, uint16_t value) {
            at[0] = static_cast<uint8_t>(value & 0xFFU);
            at[1] = static_cast<uint8_t>((value >> 8) & 0xFFU);
        }

        std::array<uint8_t, WavWriter::kHeaderBytes> makeHeader(uint32_t dataBytes) {
            std::array<uint8_t, WavWriter::kHeaderBytes> header{};
            std::memcpy(header.data() + 0, "RIFF", 4);
            put32(header.data() + 4, 36U + dataBytes);
            std::memcpy(header.data() + 8, "WAVE", 4);
            std::memcpy(header.data() + 12, "fmt ", 4);
            put32(header.data() + 16, 16U); // canonical fmt chunk
            put16(header.data() + 20, 1U);  // PCM
            put16(header.data() + 22, WavWriter::kChannels);
            put32(header.data() + 24, WavWriter::kSampleRateHz);
            put32(header.data() + 28, kByteRate);
            put16(header.data() + 32, kBlockAlign);
            put16(header.data() + 34, WavWriter::kBitsPerSample);
            std::memcpy(header.data() + 36, "data", 4);
            put32(header.data() + 40, dataBytes);
            return header;
        }

    } // namespace

    WavWriter::WavWriter(File& file) : file_(file), writer_(file) {}

    std::expected<void, std::error_code> WavWriter::begin() {
        if (started_) {
            return std::unexpected(std::make_error_code(std::errc::operation_in_progress));
        }
        const auto header = makeHeader(0);
        if (auto written = writer_.write(header.data(), header.size()); !written) {
            return written;
        }
        started_ = true;
        return {};
    }

    std::expected<void, std::error_code> WavWriter::writeSamples(const int16_t* samples, size_t count) {
        if (!started_ || finished_) {
            return std::unexpected(std::make_error_code(std::errc::operation_not_permitted));
        }
        if (count == 0) {
            return {};
        }
        // int16_t is already little-endian on xtensa and on the native test host, so the
        // sample block goes out verbatim rather than byte by byte.
        if (auto written = writer_.write(samples, count * sizeof(int16_t)); !written) {
            return written;
        }
        frames_ += static_cast<uint32_t>(count / kChannels);
        return {};
    }

    std::expected<void, std::error_code> WavWriter::finish() {
        if (!started_ || finished_) {
            return std::unexpected(std::make_error_code(std::errc::operation_not_permitted));
        }
        if (auto flushed = writer_.flush(); !flushed) {
            return flushed;
        }

        const uint32_t dataBytes = frames_ * kBlockAlign;
        const auto header = makeHeader(dataBytes);

        // Patch only the two size fields; never rewrite the chunk identifiers.
        if (auto sought = writer_.seek(kRiffSizeOffset); !sought) {
            return sought;
        }
        if (auto written = writer_.write(header.data() + kRiffSizeOffset, 4); !written) {
            return written;
        }
        if (auto sought = writer_.seek(kDataSizeOffset); !sought) {
            return sought;
        }
        if (auto written = writer_.write(header.data() + kDataSizeOffset, 4); !written) {
            return written;
        }
        if (auto flushed = writer_.flush(); !flushed) {
            return flushed;
        }
        finished_ = true;
        return {};
    }

} // namespace voice
