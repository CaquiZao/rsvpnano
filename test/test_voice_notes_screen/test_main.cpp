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


    // --- a faixa que a lista recebe -----------------------------------------

    void test_the_list_stops_above_the_button_row() {
        // The complaint that prompted this: the back button was being pushed off the
        // bottom as recordings piled up. The controls are placed first, always.
        const ui::Rect view = screens::listViewport({0, 0, 320, 148});
        TEST_ASSERT_TRUE(view.y + view.h < 148);
    }

    void test_the_list_starts_below_the_header() {
        const ui::Rect view = screens::listViewport({0, 0, 320, 148});
        TEST_ASSERT_TRUE(view.y >= 26);
    }

    void test_a_panel_too_short_for_both_gives_the_list_nothing() {
        // Zero rows is a readable screen. A negative height goes straight to fillRect.
        TEST_ASSERT_EQUAL_INT16(0, screens::listViewport({0, 0, 320, 40}).h);
    }

    void test_the_viewport_never_has_negative_height() {
        for (int16_t height = 0; height < 200; ++height) {
            TEST_ASSERT_TRUE(screens::listViewport({0, 0, 320, height}).h >= 0);
        }
    }

    void test_the_viewport_keeps_the_full_width_and_origin() {
        const ui::Rect view = screens::listViewport({7, 3, 320, 148});
        TEST_ASSERT_EQUAL_INT16(7, view.x);
        TEST_ASSERT_EQUAL_INT16(320, view.w);
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
    RUN_TEST(test_the_list_stops_above_the_button_row);
    RUN_TEST(test_the_list_starts_below_the_header);
    RUN_TEST(test_a_panel_too_short_for_both_gives_the_list_nothing);
    RUN_TEST(test_the_viewport_never_has_negative_height);
    RUN_TEST(test_the_viewport_keeps_the_full_width_and_origin);
    return UNITY_END();
}
