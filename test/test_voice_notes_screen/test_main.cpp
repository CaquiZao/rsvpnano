#include <unity.h>

#include <string>

#include "ui/screens/VoiceNotesScreen.h"

namespace {

    // --- rotulo -------------------------------------------------------------

    void test_a_stamped_recording_shows_day_and_time() {
        TEST_ASSERT_EQUAL_STRING("09/09 14:03",
                                 screens::labelFromRecordingName("20260909-140300.wav").c_str());
    }

    void test_a_recording_taken_without_a_clock_says_so() {
        // Inventing a date for it would be worse than admitting there is none.
        TEST_ASSERT_EQUAL_STRING("sem relogio", screens::labelFromRecordingName("boot-00009022.wav").c_str());
    }

    void test_an_unrecognised_name_is_shown_verbatim() {
        TEST_ASSERT_EQUAL_STRING("qualquer", screens::labelFromRecordingName("qualquer.wav").c_str());
    }

    // --- duracao ------------------------------------------------------------

    void test_a_short_note_reads_in_seconds() {
        TEST_ASSERT_EQUAL_STRING("17s", screens::formatDuration(16850).c_str());
    }

    void test_a_long_note_reads_in_minutes_and_seconds() {
        TEST_ASSERT_EQUAL_STRING("2m05s", screens::formatDuration(125000).c_str());
    }

    void test_a_note_just_under_a_minute_stays_in_seconds() {
        TEST_ASSERT_EQUAL_STRING("59s", screens::formatDuration(59400).c_str());
    }

    void test_an_empty_recording_is_zero_not_blank() {
        TEST_ASSERT_EQUAL_STRING("0s", screens::formatDuration(0).c_str());
    }

    // --- quantas linhas cabem -----------------------------------------------

    void test_the_list_gets_whatever_the_controls_do_not_need() {
        TEST_ASSERT_EQUAL_UINT32(4, screens::visibleRowCount(200, 26, 96));
    }

    void test_side_by_side_controls_leave_room_for_several_notes() {
        // The real geometry: a 148 px content area with one row of buttons reserving
        // 52 px. Stacked buttons reserved 122 px and left room for a single note, which
        // is what made the screen look broken.
        TEST_ASSERT_EQUAL_UINT32(4, screens::visibleRowCount(148, 24, 52));
    }

    void test_a_short_panel_shows_no_rows_rather_than_hiding_the_buttons() {
        // The complaint that prompted this: the back button was being pushed off the
        // bottom as recordings piled up. The controls win, always.
        TEST_ASSERT_EQUAL_UINT32(0, screens::visibleRowCount(100, 26, 96));
    }

    void test_exactly_one_row_fits_when_there_is_room_for_exactly_one() {
        TEST_ASSERT_EQUAL_UINT32(1, screens::visibleRowCount(122, 26, 96));
    }

    void test_a_zero_row_height_does_not_divide_by_zero() {
        TEST_ASSERT_EQUAL_UINT32(0, screens::visibleRowCount(200, 0, 96));
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_a_stamped_recording_shows_day_and_time);
    RUN_TEST(test_a_recording_taken_without_a_clock_says_so);
    RUN_TEST(test_an_unrecognised_name_is_shown_verbatim);
    RUN_TEST(test_a_short_note_reads_in_seconds);
    RUN_TEST(test_a_long_note_reads_in_minutes_and_seconds);
    RUN_TEST(test_a_note_just_under_a_minute_stays_in_seconds);
    RUN_TEST(test_an_empty_recording_is_zero_not_blank);
    RUN_TEST(test_the_list_gets_whatever_the_controls_do_not_need);
    RUN_TEST(test_side_by_side_controls_leave_room_for_several_notes);
    RUN_TEST(test_a_short_panel_shows_no_rows_rather_than_hiding_the_buttons);
    RUN_TEST(test_exactly_one_row_fits_when_there_is_room_for_exactly_one);
    RUN_TEST(test_a_zero_row_height_does_not_divide_by_zero);
    return UNITY_END();
}
