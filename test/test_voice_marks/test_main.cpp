#include <unity.h>

#include "voice/VoiceMarksPlan.h"

namespace {

    // --- caminho do arquivo -------------------------------------------------

    void test_each_book_gets_its_own_file() {
        TEST_ASSERT_EQUAL_STRING("/voice/marks/sapiens.txt", voice::marksPath("sapiens").c_str());
    }

    void test_a_book_with_no_slug_still_yields_a_usable_path() {
        // Recordings made outside the reader have no book. They share one file rather
        // than writing to "/voice/marks/.txt", which some drivers reject.
        TEST_ASSERT_EQUAL_STRING("/voice/marks/_.txt", voice::marksPath("").c_str());
    }

    // --- leitura ------------------------------------------------------------

    void test_one_offset_per_line() {
        const auto marks = voice::parseMarks("10\n250\n1318\n");
        TEST_ASSERT_EQUAL_UINT32(3, marks.size());
        TEST_ASSERT_EQUAL_UINT32(10, marks[0]);
        TEST_ASSERT_EQUAL_UINT32(1318, marks[2]);
    }

    void test_an_empty_file_has_no_marks() {
        TEST_ASSERT_EQUAL_UINT32(0, voice::parseMarks("").size());
    }

    void test_a_truncated_last_line_is_still_read() {
        // The device can lose power mid-append. The digits that did land are a real
        // position and worth keeping.
        const auto marks = voice::parseMarks("10\n25");
        TEST_ASSERT_EQUAL_UINT32(2, marks.size());
        TEST_ASSERT_EQUAL_UINT32(25, marks[1]);
    }

    void test_blank_and_junk_lines_are_skipped_not_counted_as_zero() {
        // A stray blank line must not put a mark at the start of the book.
        const auto marks = voice::parseMarks("10\n\n  \nabc\n30\n");
        TEST_ASSERT_EQUAL_UINT32(2, marks.size());
        TEST_ASSERT_EQUAL_UINT32(10, marks[0]);
        TEST_ASSERT_EQUAL_UINT32(30, marks[1]);
    }

    void test_carriage_returns_do_not_break_parsing() {
        const auto marks = voice::parseMarks("10\r\n30\r\n");
        TEST_ASSERT_EQUAL_UINT32(2, marks.size());
        TEST_ASSERT_EQUAL_UINT32(30, marks[1]);
    }

    void test_a_zero_offset_is_a_real_position() {
        // The very first word of a book is a legitimate place to take a note.
        const auto marks = voice::parseMarks("0\n");
        TEST_ASSERT_EQUAL_UINT32(1, marks.size());
        TEST_ASSERT_EQUAL_UINT32(0, marks[0]);
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_each_book_gets_its_own_file);
    RUN_TEST(test_a_book_with_no_slug_still_yields_a_usable_path);
    RUN_TEST(test_one_offset_per_line);
    RUN_TEST(test_an_empty_file_has_no_marks);
    RUN_TEST(test_a_truncated_last_line_is_still_read);
    RUN_TEST(test_blank_and_junk_lines_are_skipped_not_counted_as_zero);
    RUN_TEST(test_carriage_returns_do_not_break_parsing);
    RUN_TEST(test_a_zero_offset_is_a_real_position);
    return UNITY_END();
}
