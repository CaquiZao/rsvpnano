#include <unity.h>

#include <string>

#include "voice/VoiceNoteMeta.h"

namespace {

    bool contains(const std::string& haystack, const char* needle) {
        return haystack.find(needle) != std::string::npos;
    }

    // --- o que o bridge sempre recebe -------------------------------------

    void test_json_always_carries_the_clock_state() {
        voice::NoteMeta meta;
        meta.recordedAt = "2026-09-09T14:03:00";
        meta.clockSynced = true;
        meta.durationMs = 21000;

        const std::string json = voice::toJson(meta);
        TEST_ASSERT_TRUE(contains(json, "\"clock_synced\":true"));
        TEST_ASSERT_TRUE(contains(json, "\"recorded_at\":\"2026-09-09T14:03:00\""));
        TEST_ASSERT_TRUE(contains(json, "\"duration_ms\":21000"));
    }

    void test_an_unsynced_clock_is_reported_as_false_not_omitted() {
        // The bridge needs to know the timestamp is untrustworthy, so this key must
        // never disappear the way the optional ones do.
        const std::string json = voice::toJson({});
        TEST_ASSERT_TRUE(contains(json, "\"clock_synced\":false"));
    }

    // --- o que só uma nota do leitor recebe -------------------------------

    void test_a_loose_note_omits_the_book_keys_entirely() {
        voice::NoteMeta meta;
        meta.recordedAt = "2026-09-09T14:03:00";

        const std::string json = voice::toJson(meta);
        TEST_ASSERT_FALSE(contains(json, "\"book\""));
        TEST_ASSERT_FALSE(contains(json, "\"excerpt\""));
        TEST_ASSERT_FALSE(contains(json, "\"word_offset\""));
    }

    void test_a_reader_note_carries_the_anchor() {
        voice::NoteMeta meta;
        meta.book = "epdf.pub_sapiens";
        meta.wordOffset = 12438;
        meta.excerpt = "a Revolucao Agricola foi a maior fraude da historia";

        const std::string json = voice::toJson(meta);
        TEST_ASSERT_TRUE(contains(json, "\"book\":\"epdf.pub_sapiens\""));
        TEST_ASSERT_TRUE(contains(json, "\"word_offset\":12438"));
        TEST_ASSERT_TRUE(contains(json, "maior fraude"));
    }

    void test_word_offset_zero_still_ships_when_there_is_a_book() {
        // The first word of a book is a legitimate anchor. Treating 0 as absent would
        // silently drop the anchor for anyone recording on page one.
        voice::NoteMeta meta;
        meta.book = "livro";
        meta.wordOffset = 0;

        TEST_ASSERT_TRUE(contains(voice::toJson(meta), "\"word_offset\":0"));
    }

    // --- escape ------------------------------------------------------------

    void test_a_quote_in_the_excerpt_does_not_break_the_json() {
        voice::NoteMeta meta;
        // The excerpt only ships alongside a book, because a passage with no book to
        // quote it from is meaningless.
        meta.book = "livro";
        meta.excerpt = "ele disse \"nao\" e saiu";
        TEST_ASSERT_TRUE(contains(voice::toJson(meta), "\\\"nao\\\""));
    }

    void test_a_backslash_is_escaped() {
        TEST_ASSERT_EQUAL_STRING("a\\\\b", voice::escapeJsonString("a\\b").c_str());
    }

    void test_a_newline_becomes_an_escape_not_a_raw_break() {
        TEST_ASSERT_EQUAL_STRING("linha\\numa", voice::escapeJsonString("linha\numa").c_str());
    }

    void test_a_control_character_becomes_a_unicode_escape() {
        TEST_ASSERT_EQUAL_STRING("a\\u0001b", voice::escapeJsonString("a\x01" "b").c_str());
    }

    void test_accented_utf8_passes_through_untouched() {
        // The excerpt is Portuguese. Escaping it to \u would make every sidecar
        // unreadable for no gain: JSON is UTF-8 by definition.
        const char* accented = "cora\xc3\xa7\xc3\xa3o";
        TEST_ASSERT_EQUAL_STRING(accented, voice::escapeJsonString(accented).c_str());
    }

    // --- caminho do sidecar ------------------------------------------------

    void test_sidecar_path_replaces_the_extension() {
        TEST_ASSERT_EQUAL_STRING("/voice/20260909-140300.json",
                                 voice::sidecarPath("/voice/20260909-140300.wav").c_str());
    }

    void test_sidecar_path_of_a_name_without_an_extension_appends_one() {
        TEST_ASSERT_EQUAL_STRING("/voice/nota.json", voice::sidecarPath("/voice/nota").c_str());
    }

    void test_a_dot_in_the_directory_is_not_mistaken_for_the_extension() {
        TEST_ASSERT_EQUAL_STRING("/v.1/nota.json", voice::sidecarPath("/v.1/nota.wav").c_str());
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_json_always_carries_the_clock_state);
    RUN_TEST(test_an_unsynced_clock_is_reported_as_false_not_omitted);
    RUN_TEST(test_a_loose_note_omits_the_book_keys_entirely);
    RUN_TEST(test_a_reader_note_carries_the_anchor);
    RUN_TEST(test_word_offset_zero_still_ships_when_there_is_a_book);
    RUN_TEST(test_a_quote_in_the_excerpt_does_not_break_the_json);
    RUN_TEST(test_a_backslash_is_escaped);
    RUN_TEST(test_a_newline_becomes_an_escape_not_a_raw_break);
    RUN_TEST(test_a_control_character_becomes_a_unicode_escape);
    RUN_TEST(test_accented_utf8_passes_through_untouched);
    RUN_TEST(test_sidecar_path_replaces_the_extension);
    RUN_TEST(test_sidecar_path_of_a_name_without_an_extension_appends_one);
    RUN_TEST(test_a_dot_in_the_directory_is_not_mistaken_for_the_extension);
    return UNITY_END();
}
