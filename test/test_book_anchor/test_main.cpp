#include <unity.h>

#include <string>

#include "voice/BookAnchor.h"

namespace {

    // --- slug do livro ------------------------------------------------------

    void test_the_slug_drops_the_directory_and_the_extension() {
        TEST_ASSERT_EQUAL_STRING("epdf.pub_sapiens",
                                 voice::bookSlug("/books/epdf.pub_sapiens.rsvp").c_str());
    }

    void test_a_dotted_name_keeps_every_dot_but_the_last() {
        // "epdf.pub_..." is a real file name from the user's vault; treating the first
        // dot as the extension would truncate the slug to "epdf".
        TEST_ASSERT_EQUAL_STRING("a.b.c", voice::bookSlug("/books/a.b.c.rsvp").c_str());
    }

    void test_a_bare_name_needs_no_directory() {
        TEST_ASSERT_EQUAL_STRING("livro", voice::bookSlug("livro.rsvp").c_str());
    }

    void test_a_name_without_an_extension_is_kept_whole() {
        TEST_ASSERT_EQUAL_STRING("livro", voice::bookSlug("/books/livro").c_str());
    }

    void test_no_path_yields_no_slug() {
        TEST_ASSERT_EQUAL_STRING("", voice::bookSlug("").c_str());
    }

    // --- corte do trecho ----------------------------------------------------

    void test_a_short_paragraph_is_kept_whole() {
        TEST_ASSERT_EQUAL_STRING("um trecho curto", voice::clampExcerpt("um trecho curto", 400).c_str());
    }

    void test_a_long_paragraph_is_cut_on_a_word_boundary() {
        TEST_ASSERT_EQUAL_STRING("alfa beta", voice::clampExcerpt("alfa beta gama delta", 12).c_str());
    }

    void test_the_cut_leaves_no_trailing_space() {
        TEST_ASSERT_EQUAL_STRING("alfa", voice::clampExcerpt("alfa beta", 5).c_str());
    }

    void test_a_single_word_longer_than_the_limit_is_cut_anyway() {
        // Refusing to cut would hand the bridge a 4 KB excerpt from a malformed book.
        const std::string cut = voice::clampExcerpt("abcdefghijklmnop", 5);
        TEST_ASSERT_EQUAL_UINT32(5, cut.size());
    }

    void test_the_cut_never_splits_a_utf8_character() {
        // "coração": the cedilla occupies bytes 5 and 6, so a cut at 5 lands inside it.
        // The whole character has to go, not just its lead byte: an orphan lead byte is
        // equally invalid and much harder to spot.
        TEST_ASSERT_EQUAL_STRING("cora", voice::clampExcerpt("cora\xc3\xa7\xc3\xa3o", 5).c_str());
    }

    void test_a_cut_that_lands_on_a_character_boundary_keeps_that_character() {
        // Byte 6 is the end of the cedilla, so nothing needs dropping.
        TEST_ASSERT_EQUAL_STRING("cora\xc3\xa7", voice::clampExcerpt("cora\xc3\xa7\xc3\xa3o", 6).c_str());
    }

    void test_a_cut_inside_a_three_byte_character_drops_the_whole_character() {
        // An em dash is three bytes in UTF-8.
        TEST_ASSERT_EQUAL_STRING("ab", voice::clampExcerpt("ab\xe2\x80\x94cd", 4).c_str());
    }

    void test_an_accented_paragraph_within_the_limit_survives_intact() {
        const char* accented = "cora\xc3\xa7\xc3\xa3o";
        TEST_ASSERT_EQUAL_STRING(accented, voice::clampExcerpt(accented, 400).c_str());
    }

    void test_internal_whitespace_is_collapsed() {
        // The reader stores words, so a paragraph rebuilt from them can carry runs of
        // spaces and newlines. They would waste the excerpt budget.
        TEST_ASSERT_EQUAL_STRING("alfa beta", voice::clampExcerpt("alfa \n\t beta", 400).c_str());
    }

    void test_surrounding_whitespace_is_trimmed() {
        TEST_ASSERT_EQUAL_STRING("alfa", voice::clampExcerpt("  alfa  ", 400).c_str());
    }

    void test_an_empty_paragraph_yields_an_empty_excerpt() {
        TEST_ASSERT_EQUAL_STRING("", voice::clampExcerpt("", 400).c_str());
    }

    void test_a_zero_limit_yields_nothing_rather_than_everything() {
        TEST_ASSERT_EQUAL_STRING("", voice::clampExcerpt("alfa beta", 0).c_str());
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_the_slug_drops_the_directory_and_the_extension);
    RUN_TEST(test_a_dotted_name_keeps_every_dot_but_the_last);
    RUN_TEST(test_a_bare_name_needs_no_directory);
    RUN_TEST(test_a_name_without_an_extension_is_kept_whole);
    RUN_TEST(test_no_path_yields_no_slug);
    RUN_TEST(test_a_short_paragraph_is_kept_whole);
    RUN_TEST(test_a_long_paragraph_is_cut_on_a_word_boundary);
    RUN_TEST(test_the_cut_leaves_no_trailing_space);
    RUN_TEST(test_a_single_word_longer_than_the_limit_is_cut_anyway);
    RUN_TEST(test_the_cut_never_splits_a_utf8_character);
    RUN_TEST(test_a_cut_that_lands_on_a_character_boundary_keeps_that_character);
    RUN_TEST(test_a_cut_inside_a_three_byte_character_drops_the_whole_character);
    RUN_TEST(test_an_accented_paragraph_within_the_limit_survives_intact);
    RUN_TEST(test_internal_whitespace_is_collapsed);
    RUN_TEST(test_surrounding_whitespace_is_trimmed);
    RUN_TEST(test_an_empty_paragraph_yields_an_empty_excerpt);
    RUN_TEST(test_a_zero_limit_yields_nothing_rather_than_everything);
    return UNITY_END();
}
