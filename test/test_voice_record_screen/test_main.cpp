#include <unity.h>

#include "ui/screens/VoiceRecordScreen.h"

namespace {

    // --- altura das barras --------------------------------------------------

    void test_silence_draws_a_visible_baseline_not_nothing() {
        // A bar of height zero looks like the screen froze. The 1 px floor is what
        // tells the user the device is still listening.
        TEST_ASSERT_EQUAL_UINT8(1, screens::waveformBarHeight(0, 40));
    }

    void test_full_level_fills_the_bar() {
        TEST_ASSERT_EQUAL_UINT8(40, screens::waveformBarHeight(255, 40));
    }

    void test_the_mapping_never_goes_backwards() {
        uint8_t previous = 0;
        for (int level = 0; level <= 255; ++level) {
            const uint8_t height = screens::waveformBarHeight(static_cast<uint8_t>(level), 40);
            TEST_ASSERT_TRUE(height >= previous);
            previous = height;
        }
    }

    void test_the_bar_never_exceeds_the_space_it_was_given() {
        for (int level = 0; level <= 255; ++level) {
            TEST_ASSERT_TRUE(screens::waveformBarHeight(static_cast<uint8_t>(level), 7) <= 7);
        }
    }

    void test_a_zero_height_area_draws_nothing() {
        // Guards the caller that lays the screen out before knowing its size.
        TEST_ASSERT_EQUAL_UINT8(0, screens::waveformBarHeight(255, 0));
    }

    // --- tempo decorrido ----------------------------------------------------

    void test_the_first_second_reads_as_zero() {
        TEST_ASSERT_EQUAL_STRING("0:00", screens::formatElapsed(0).c_str());
        TEST_ASSERT_EQUAL_STRING("0:00", screens::formatElapsed(999).c_str());
    }

    void test_seconds_are_padded_to_two_digits() {
        TEST_ASSERT_EQUAL_STRING("0:07", screens::formatElapsed(7000).c_str());
    }

    void test_a_minute_rolls_over() {
        TEST_ASSERT_EQUAL_STRING("1:00", screens::formatElapsed(60000).c_str());
        TEST_ASSERT_EQUAL_STRING("12:34", screens::formatElapsed((12 * 60 + 34) * 1000).c_str());
    }

    void test_past_an_hour_the_minutes_keep_counting() {
        // Recording has no length limit, so the display must not wrap or mislead.
        TEST_ASSERT_EQUAL_STRING("61:02", screens::formatElapsed((61 * 60 + 2) * 1000).c_str());
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_silence_draws_a_visible_baseline_not_nothing);
    RUN_TEST(test_full_level_fills_the_bar);
    RUN_TEST(test_the_mapping_never_goes_backwards);
    RUN_TEST(test_the_bar_never_exceeds_the_space_it_was_given);
    RUN_TEST(test_a_zero_height_area_draws_nothing);
    RUN_TEST(test_the_first_second_reads_as_zero);
    RUN_TEST(test_seconds_are_padded_to_two_digits);
    RUN_TEST(test_a_minute_rolls_over);
    RUN_TEST(test_past_an_hour_the_minutes_keep_counting);
    return UNITY_END();
}
