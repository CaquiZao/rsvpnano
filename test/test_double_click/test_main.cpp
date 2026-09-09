#include <unity.h>

#include "voice/DoubleClick.h"

namespace {

    using Verdict = voice::DoubleClick::Verdict;

    void assertVerdict(Verdict expected, Verdict actual) {
        TEST_ASSERT_EQUAL_INT(static_cast<int>(expected), static_cast<int>(actual));
    }

    void test_one_press_alone_becomes_a_single_only_after_the_window() {
        voice::DoubleClick detector;
        assertVerdict(Verdict::Nothing, detector.onPress(1000));
        assertVerdict(Verdict::Nothing, detector.tick(1100));
        assertVerdict(Verdict::Single, detector.tick(1300));
    }

    void test_the_single_is_reported_exactly_once() {
        // Repeating it every frame would toggle play/pause forever.
        voice::DoubleClick detector;
        detector.onPress(1000);
        assertVerdict(Verdict::Single, detector.tick(1300));
        assertVerdict(Verdict::Nothing, detector.tick(1400));
    }

    void test_two_presses_inside_the_window_are_a_double() {
        voice::DoubleClick detector;
        detector.onPress(1000);
        assertVerdict(Verdict::Double, detector.onPress(1150));
    }

    void test_two_presses_outside_the_window_are_two_singles() {
        voice::DoubleClick detector;
        detector.onPress(1000);
        assertVerdict(Verdict::Single, detector.tick(1300));
        detector.onPress(1400);
        assertVerdict(Verdict::Single, detector.tick(1700));
    }

    void test_a_double_does_not_leak_a_single_afterwards() {
        voice::DoubleClick detector;
        detector.onPress(1000);
        detector.onPress(1100);
        assertVerdict(Verdict::Nothing, detector.tick(1500));
    }

    void test_a_third_press_starts_a_fresh_window() {
        // Triple tapping should not fire a second double off the same middle press.
        voice::DoubleClick detector;
        detector.onPress(1000);
        detector.onPress(1100);
        assertVerdict(Verdict::Nothing, detector.onPress(1200));
        assertVerdict(Verdict::Single, detector.tick(1500));
    }

    void test_a_press_exactly_on_the_boundary_counts_as_a_double() {
        // The boundary has to land somewhere; giving it to the double means a user who
        // taps a touch too slowly still gets what they meant.
        voice::DoubleClick detector;
        detector.onPress(1000);
        assertVerdict(Verdict::Double, detector.onPress(1000 + voice::DoubleClick::kWindowMs));
    }

    void test_millis_wrapping_does_not_swallow_a_click() {
        // millis() wraps every 49 days. Unsigned subtraction handles it with no special
        // case, but only if nothing compares the timestamps directly.
        voice::DoubleClick detector;
        detector.onPress(0xFFFFFFF0u);
        assertVerdict(Verdict::Double, detector.onPress(0x00000050u));
    }

    void test_wrapping_also_expires_a_pending_single() {
        voice::DoubleClick detector;
        detector.onPress(0xFFFFFFF0u);
        assertVerdict(Verdict::Single, detector.tick(0x00000200u));
    }

    void test_reset_drops_a_pending_press() {
        // Leaving the reader mid-window must not fire a play/pause on the next screen.
        voice::DoubleClick detector;
        detector.onPress(1000);
        detector.reset();
        assertVerdict(Verdict::Nothing, detector.tick(1300));
    }

    void test_a_tick_with_nothing_pending_is_quiet() {
        voice::DoubleClick detector;
        assertVerdict(Verdict::Nothing, detector.tick(5000));
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_one_press_alone_becomes_a_single_only_after_the_window);
    RUN_TEST(test_the_single_is_reported_exactly_once);
    RUN_TEST(test_two_presses_inside_the_window_are_a_double);
    RUN_TEST(test_two_presses_outside_the_window_are_two_singles);
    RUN_TEST(test_a_double_does_not_leak_a_single_afterwards);
    RUN_TEST(test_a_third_press_starts_a_fresh_window);
    RUN_TEST(test_a_press_exactly_on_the_boundary_counts_as_a_double);
    RUN_TEST(test_millis_wrapping_does_not_swallow_a_click);
    RUN_TEST(test_wrapping_also_expires_a_pending_single);
    RUN_TEST(test_reset_drops_a_pending_press);
    RUN_TEST(test_a_tick_with_nothing_pending_is_quiet);
    return UNITY_END();
}
