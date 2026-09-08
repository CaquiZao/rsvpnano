#include <unity.h>

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

#include <FS.h>

#include "voice/WavWriter.h"

namespace {

    // O shim de teste expoe contents() como std::string, nao um vetor de bytes.
    uint8_t byteAt(const std::string& b, size_t at) {
        return static_cast<uint8_t>(b[at]);
    }
    uint32_t le32(const std::string& b, size_t at) {
        return static_cast<uint32_t>(byteAt(b, at)) | (static_cast<uint32_t>(byteAt(b, at + 1)) << 8)
            | (static_cast<uint32_t>(byteAt(b, at + 2)) << 16)
            | (static_cast<uint32_t>(byteAt(b, at + 3)) << 24);
    }
    uint16_t le16(const std::string& b, size_t at) {
        return static_cast<uint16_t>(static_cast<uint16_t>(byteAt(b, at))
                                     | (static_cast<uint16_t>(byteAt(b, at + 1)) << 8));
    }
    bool tagAt(const std::string& b, size_t at, const char* tag) {
        return std::memcmp(b.data() + at, tag, 4) == 0;
    }

    void test_header_describes_16k_mono_16bit() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());
        TEST_ASSERT_TRUE(writer.finish().has_value());

        const std::string& b = file.contents();
        TEST_ASSERT_EQUAL_UINT32(voice::WavWriter::kHeaderBytes, b.size());
        TEST_ASSERT_TRUE(tagAt(b, 0, "RIFF"));
        TEST_ASSERT_TRUE(tagAt(b, 8, "WAVE"));
        TEST_ASSERT_TRUE(tagAt(b, 12, "fmt "));
        TEST_ASSERT_EQUAL_UINT32(16, le32(b, 16));
        TEST_ASSERT_EQUAL_UINT16(1, le16(b, 20));
        TEST_ASSERT_EQUAL_UINT16(1, le16(b, 22));
        TEST_ASSERT_EQUAL_UINT32(16000, le32(b, 24));
        TEST_ASSERT_EQUAL_UINT32(32000, le32(b, 28));
        TEST_ASSERT_EQUAL_UINT16(2, le16(b, 32));
        TEST_ASSERT_EQUAL_UINT16(16, le16(b, 34));
        TEST_ASSERT_TRUE(tagAt(b, 36, "data"));
    }

    void test_finish_patches_both_sizes() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());

        std::vector<int16_t> samples(16000, 1234);
        TEST_ASSERT_TRUE(writer.writeSamples(samples.data(), samples.size()).has_value());
        TEST_ASSERT_TRUE(writer.finish().has_value());

        const std::string& b = file.contents();
        const uint32_t payload = 16000 * 2;
        TEST_ASSERT_EQUAL_UINT32(voice::WavWriter::kHeaderBytes + payload, b.size());
        TEST_ASSERT_EQUAL_UINT32(36 + payload, le32(b, 4));
        TEST_ASSERT_EQUAL_UINT32(payload, le32(b, 40));
        // O identificador do chunk nao pode ter sido sobrescrito pela correcao
        TEST_ASSERT_TRUE(tagAt(b, 36, "data"));
    }

    void test_samples_are_written_little_endian_in_order() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());
        const int16_t samples[] = {0x0102, static_cast<int16_t>(0xFF7F), 0x0000};
        TEST_ASSERT_TRUE(writer.writeSamples(samples, 3).has_value());
        TEST_ASSERT_TRUE(writer.finish().has_value());

        const std::string& b = file.contents();
        const size_t at = voice::WavWriter::kHeaderBytes;
        TEST_ASSERT_EQUAL_UINT8(0x02, byteAt(b, at + 0));
        TEST_ASSERT_EQUAL_UINT8(0x01, byteAt(b, at + 1));
        TEST_ASSERT_EQUAL_UINT8(0x7F, byteAt(b, at + 2));
        TEST_ASSERT_EQUAL_UINT8(0xFF, byteAt(b, at + 3));
    }

    void test_counts_frames_and_duration() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());
        std::vector<int16_t> samples(8000, 0);
        TEST_ASSERT_TRUE(writer.writeSamples(samples.data(), samples.size()).has_value());
        TEST_ASSERT_EQUAL_UINT32(8000, writer.frameCount());
        TEST_ASSERT_EQUAL_UINT32(500, writer.durationMs());
    }

    void test_many_small_writes_match_one_big_write() {
        File a;
        voice::WavWriter wa(a);
        TEST_ASSERT_TRUE(wa.begin().has_value());
        std::vector<int16_t> block(1024);
        for (size_t i = 0; i < block.size(); ++i) {
            block[i] = static_cast<int16_t>(i);
        }
        for (int n = 0; n < 16; ++n) {
            TEST_ASSERT_TRUE(wa.writeSamples(block.data(), block.size()).has_value());
        }
        TEST_ASSERT_TRUE(wa.finish().has_value());

        File b;
        voice::WavWriter wb(b);
        TEST_ASSERT_TRUE(wb.begin().has_value());
        std::vector<int16_t> all;
        for (int n = 0; n < 16; ++n) {
            all.insert(all.end(), block.begin(), block.end());
        }
        TEST_ASSERT_TRUE(wb.writeSamples(all.data(), all.size()).has_value());
        TEST_ASSERT_TRUE(wb.finish().has_value());

        TEST_ASSERT_EQUAL_UINT32(a.contents().size(), b.contents().size());
        TEST_ASSERT_EQUAL_INT(0, std::memcmp(a.contents().data(), b.contents().data(), a.contents().size()));
    }

    void test_write_without_begin_fails() {
        File file;
        voice::WavWriter writer(file);
        const int16_t sample = 0;
        TEST_ASSERT_FALSE(writer.writeSamples(&sample, 1).has_value());
    }

    void test_zero_length_recording_still_produces_valid_header() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());
        TEST_ASSERT_TRUE(writer.finish().has_value());
        TEST_ASSERT_EQUAL_UINT32(0, le32(file.contents(), 40));
        TEST_ASSERT_EQUAL_UINT32(0, writer.frameCount());
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_header_describes_16k_mono_16bit);
    RUN_TEST(test_finish_patches_both_sizes);
    RUN_TEST(test_samples_are_written_little_endian_in_order);
    RUN_TEST(test_counts_frames_and_duration);
    RUN_TEST(test_many_small_writes_match_one_big_write);
    RUN_TEST(test_write_without_begin_fails);
    RUN_TEST(test_zero_length_recording_still_produces_valid_header);
    return UNITY_END();
}
