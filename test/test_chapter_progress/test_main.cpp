#include <unity.h>

#include <array>
#include <string>

#include "reader/ChapterProgress.h"

namespace {

    // --- livro sem capitulos ------------------------------------------------

    void test_a_book_without_chapters_reports_only_the_book_percent() {
        const auto position = reading::chapterPositionAt({}, 50, 100);
        TEST_ASSERT_EQUAL_UINT32(0, position.count);
        TEST_ASSERT_EQUAL_UINT8(50, position.percentInBook);
    }

    void test_an_empty_book_does_not_divide_by_zero() {
        const auto position = reading::chapterPositionAt({}, 0, 0);
        TEST_ASSERT_EQUAL_UINT8(0, position.percentInBook);
        TEST_ASSERT_EQUAL_UINT8(0, position.percentInChapter);
    }

    // --- o caso que motivou tudo isto ---------------------------------------

    void test_the_middle_of_a_chapter_reads_fifty_percent_even_at_two_percent_of_the_book() {
        // The complaint in one test: halfway through chapter two of a long book, the
        // book bar has barely moved but the chapter bar says 50%.
        const std::array<ChapterMarker, 3> chapters = {ChapterMarker{"um", 0}, ChapterMarker{"dois", 100},
                                                       ChapterMarker{"tres", 200}};
        const auto position = reading::chapterPositionAt(chapters, 150, 10000);
        TEST_ASSERT_EQUAL_UINT32(1, position.index);
        TEST_ASSERT_EQUAL_UINT8(50, position.percentInChapter);
        TEST_ASSERT_EQUAL_UINT8(1, position.percentInBook);
        TEST_ASSERT_EQUAL_STRING("dois", std::string(position.title).c_str());
    }

    void test_the_start_of_a_chapter_is_zero_percent() {
        const std::array<ChapterMarker, 2> chapters = {ChapterMarker{"um", 0}, ChapterMarker{"dois", 100}};
        TEST_ASSERT_EQUAL_UINT8(0, reading::chapterPositionAt(chapters, 100, 200).percentInChapter);
    }

    void test_the_last_chapter_runs_to_the_end_of_the_book() {
        // There is no marker after the last chapter, so its length has to come from the
        // book's word count.
        const std::array<ChapterMarker, 2> chapters = {ChapterMarker{"um", 0}, ChapterMarker{"dois", 100}};
        TEST_ASSERT_EQUAL_UINT8(50, reading::chapterPositionAt(chapters, 150, 200).percentInChapter);
    }

    void test_the_very_end_of_the_book_is_one_hundred_percent() {
        const std::array<ChapterMarker, 1> chapters = {ChapterMarker{"um", 0}};
        const auto position = reading::chapterPositionAt(chapters, 200, 200);
        TEST_ASSERT_EQUAL_UINT8(100, position.percentInChapter);
        TEST_ASSERT_EQUAL_UINT8(100, position.percentInBook);
    }

    // --- limites ------------------------------------------------------------

    void test_a_position_before_the_first_marker_belongs_to_the_first_chapter() {
        // Front matter sits before chapter one in many epubs.
        const std::array<ChapterMarker, 2> chapters = {ChapterMarker{"um", 10}, ChapterMarker{"dois", 100}};
        const auto position = reading::chapterPositionAt(chapters, 5, 200);
        TEST_ASSERT_EQUAL_UINT32(0, position.index);
        TEST_ASSERT_EQUAL_UINT8(0, position.percentInChapter);
    }

    void test_two_markers_at_the_same_word_do_not_divide_by_zero() {
        // Malformed navigation documents produce these.
        const std::array<ChapterMarker, 2> chapters = {ChapterMarker{"um", 50}, ChapterMarker{"dois", 50}};
        TEST_ASSERT_EQUAL_UINT8(0, reading::chapterPositionAt(chapters, 50, 100).percentInChapter);
    }

    void test_a_position_past_the_word_count_is_clamped() {
        // A resumed position from a differently indexed build of the same book.
        const std::array<ChapterMarker, 1> chapters = {ChapterMarker{"um", 0}};
        const auto position = reading::chapterPositionAt(chapters, 500, 200);
        TEST_ASSERT_EQUAL_UINT8(100, position.percentInChapter);
        TEST_ASSERT_EQUAL_UINT8(100, position.percentInBook);
    }

    void test_the_chapter_count_is_reported_for_the_label() {
        const std::array<ChapterMarker, 3> chapters = {ChapterMarker{"um", 0}, ChapterMarker{"dois", 100},
                                                       ChapterMarker{"tres", 200}};
        TEST_ASSERT_EQUAL_UINT32(3, reading::chapterPositionAt(chapters, 150, 300).count);
    }

    void test_an_untitled_chapter_yields_an_empty_title_not_a_crash() {
        const std::array<ChapterMarker, 1> chapters = {ChapterMarker{"", 0}};
        TEST_ASSERT_TRUE(reading::chapterPositionAt(chapters, 10, 100).title.empty());
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_a_book_without_chapters_reports_only_the_book_percent);
    RUN_TEST(test_an_empty_book_does_not_divide_by_zero);
    RUN_TEST(test_the_middle_of_a_chapter_reads_fifty_percent_even_at_two_percent_of_the_book);
    RUN_TEST(test_the_start_of_a_chapter_is_zero_percent);
    RUN_TEST(test_the_last_chapter_runs_to_the_end_of_the_book);
    RUN_TEST(test_the_very_end_of_the_book_is_one_hundred_percent);
    RUN_TEST(test_a_position_before_the_first_marker_belongs_to_the_first_chapter);
    RUN_TEST(test_two_markers_at_the_same_word_do_not_divide_by_zero);
    RUN_TEST(test_a_position_past_the_word_count_is_clamped);
    RUN_TEST(test_the_chapter_count_is_reported_for_the_label);
    RUN_TEST(test_an_untitled_chapter_yields_an_empty_title_not_a_crash);
    return UNITY_END();
}
