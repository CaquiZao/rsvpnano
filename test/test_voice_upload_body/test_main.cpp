#include <unity.h>

#include <string>

#include "voice/VoiceUploadBody.h"

namespace {

    bool contains(const std::string& haystack, const char* needle) {
        return haystack.find(needle) != std::string::npos;
    }

    void test_the_header_names_the_fields_the_bridge_expects() {
        const std::string head = voice::multipartHeader("BOUNDARY", "20260909-140300.wav", "{\"a\":1}");
        TEST_ASSERT_TRUE(contains(head, "name=\"meta\""));
        TEST_ASSERT_TRUE(contains(head, "name=\"audio\""));
        TEST_ASSERT_TRUE(contains(head, "filename=\"20260909-140300.wav\""));
        TEST_ASSERT_TRUE(contains(head, "Content-Type: audio/wav"));
        TEST_ASSERT_TRUE(contains(head, "{\"a\":1}"));
    }

    void test_the_meta_part_is_declared_as_json() {
        // Sent as text/plain the bridge would still parse it, but declaring the type is
        // what lets it reject a malformed field instead of guessing.
        const std::string head = voice::multipartHeader("B", "a.wav", "{}");
        TEST_ASSERT_TRUE(contains(head, "Content-Type: application/json"));
    }

    void test_the_header_ends_ready_for_raw_audio() {
        // The very next byte written is the first byte of the WAV, so the header has to
        // end on the blank line that closes the part's own headers.
        const std::string head = voice::multipartHeader("B", "a.wav", "{}");
        TEST_ASSERT_EQUAL_STRING("\r\n\r\n", head.substr(head.size() - 4).c_str());
    }

    void test_the_footer_closes_the_boundary() {
        TEST_ASSERT_EQUAL_STRING("\r\n--BOUNDARY--\r\n", voice::multipartFooter("BOUNDARY").c_str());
    }

    void test_the_declared_length_matches_what_gets_written() {
        const std::string head = voice::multipartHeader("B", "a.wav", "{\"x\":2}");
        const std::string foot = voice::multipartFooter("B");
        const size_t audio = 1024;
        TEST_ASSERT_EQUAL_UINT32(head.size() + audio + foot.size(),
                                 voice::multipartLength("B", "a.wav", "{\"x\":2}", audio));
    }

    void test_an_empty_recording_still_yields_a_well_formed_body() {
        // A zero-byte WAV is a failed capture, but it must not produce a body whose
        // Content-Length disagrees with the bytes sent -- that hangs the connection.
        TEST_ASSERT_EQUAL_UINT32(voice::multipartHeader("B", "a.wav", "{}").size()
                                     + voice::multipartFooter("B").size(),
                                 voice::multipartLength("B", "a.wav", "{}", 0));
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_the_header_names_the_fields_the_bridge_expects);
    RUN_TEST(test_the_meta_part_is_declared_as_json);
    RUN_TEST(test_the_header_ends_ready_for_raw_audio);
    RUN_TEST(test_the_footer_closes_the_boundary);
    RUN_TEST(test_the_declared_length_matches_what_gets_written);
    RUN_TEST(test_an_empty_recording_still_yields_a_well_formed_body);
    return UNITY_END();
}
