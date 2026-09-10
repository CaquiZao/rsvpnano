#include <unity.h>

#include "voice/CaptureLimits.h"

namespace {

    using voice::CaptureStop;
    using voice::shouldStopCapture;

    // --- bateria ------------------------------------------------------------

    void test_a_healthy_battery_keeps_recording() {
        TEST_ASSERT_EQUAL(CaptureStop::None, shouldStopCapture(80, 500u * 1024u * 1024u));
    }

    void test_a_battery_at_the_threshold_still_records() {
        // 15% is the floor, not the trigger: stopping at exactly the documented
        // number would surprise someone who read the spec.
        TEST_ASSERT_EQUAL(CaptureStop::None, shouldStopCapture(15, 500u * 1024u * 1024u));
    }

    void test_a_battery_below_the_threshold_stops() {
        TEST_ASSERT_EQUAL(CaptureStop::Battery, shouldStopCapture(14, 500u * 1024u * 1024u));
    }

    void test_an_unreadable_battery_does_not_stop_a_recording() {
        // A board with no gauge reports zero. Losing a note to a sensor that was
        // never there is worse than the flat battery this guards against.
        TEST_ASSERT_EQUAL(CaptureStop::None, shouldStopCapture(0, 500u * 1024u * 1024u));
    }

    // --- espaco -------------------------------------------------------------

    void test_plenty_of_room_keeps_recording() {
        TEST_ASSERT_EQUAL(CaptureStop::None, shouldStopCapture(80, 64u * 1024u * 1024u));
    }

    void test_a_nearly_full_card_stops() {
        // Under the reserve, which is a minute of audio plus room for the sidecar.
        TEST_ASSERT_EQUAL(CaptureStop::Disk, shouldStopCapture(80, 1u * 1024u * 1024u));
    }

    void test_a_card_reporting_nothing_free_stops() {
        TEST_ASSERT_EQUAL(CaptureStop::Disk, shouldStopCapture(80, 0));
    }

    // --- precedencia --------------------------------------------------------

    void test_a_flat_battery_on_a_full_card_reports_the_battery() {
        // Both are true; the battery is the one the user can act on by charging.
        TEST_ASSERT_EQUAL(CaptureStop::Battery, shouldStopCapture(5, 0));
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_a_healthy_battery_keeps_recording);
    RUN_TEST(test_a_battery_at_the_threshold_still_records);
    RUN_TEST(test_a_battery_below_the_threshold_stops);
    RUN_TEST(test_an_unreadable_battery_does_not_stop_a_recording);
    RUN_TEST(test_plenty_of_room_keeps_recording);
    RUN_TEST(test_a_nearly_full_card_stops);
    RUN_TEST(test_a_card_reporting_nothing_free_stops);
    RUN_TEST(test_a_flat_battery_on_a_full_card_reports_the_battery);
    return UNITY_END();
}
