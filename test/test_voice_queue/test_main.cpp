#include <unity.h>

#include <algorithm>
#include <string>
#include <vector>

#include "voice/VoiceQueuePlan.h"

namespace {

    voice::QueuePlan planOf(std::vector<std::string> names) {
        return voice::planFrom(names);
    }

    bool sweeps(const voice::QueuePlan& plan, const char* name) {
        return std::find(plan.sweep.begin(), plan.sweep.end(), std::string(name)) != plan.sweep.end();
    }

    // --- o caso normal -----------------------------------------------------

    void test_an_empty_directory_has_nothing_to_do() {
        const auto plan = planOf({});
        TEST_ASSERT_EQUAL(0, plan.pending.size());
        TEST_ASSERT_EQUAL(0, plan.sweep.size());
    }

    void test_a_recording_with_its_sidecar_is_pending() {
        const auto plan = planOf({"20260909-140300.wav", "20260909-140300.json"});
        TEST_ASSERT_EQUAL(1, plan.pending.size());
        TEST_ASSERT_EQUAL_STRING("/voice/20260909-140300.wav", plan.pending[0].wavPath.c_str());
        TEST_ASSERT_EQUAL_STRING("/voice/20260909-140300.json", plan.pending[0].metaPath.c_str());
        TEST_ASSERT_EQUAL(0, plan.sweep.size());
    }

    void test_the_queue_is_ordered_oldest_first() {
        const auto plan = planOf({"20260909-1200.wav", "20260908-0900.wav", "20260909-0100.wav"});
        TEST_ASSERT_EQUAL(3, plan.pending.size());
        TEST_ASSERT_EQUAL_STRING("/voice/20260908-0900.wav", plan.pending[0].wavPath.c_str());
        TEST_ASSERT_EQUAL_STRING("/voice/20260909-0100.wav", plan.pending[1].wavPath.c_str());
        TEST_ASSERT_EQUAL_STRING("/voice/20260909-1200.wav", plan.pending[2].wavPath.c_str());
    }

    // --- o que nunca se perde ----------------------------------------------

    void test_a_recording_without_a_sidecar_is_still_pending() {
        // Losing the note because the sidecar failed to write would be far worse than
        // sending it with no anchor.
        const auto plan = planOf({"20260909-140300.wav"});
        TEST_ASSERT_EQUAL(1, plan.pending.size());
        TEST_ASSERT_TRUE(plan.pending[0].metaPath.empty());
    }

    void test_a_parked_recording_is_neither_pending_nor_swept() {
        // Parked audio is kept for inspection, so it must survive every sweep.
        const auto plan = planOf({"20260909-140300.wav.parked"});
        TEST_ASSERT_EQUAL(0, plan.pending.size());
        TEST_ASSERT_EQUAL(0, plan.sweep.size());
    }

    // --- o que uma tentativa significa para a fila -------------------------

    void test_a_delivered_recording_leaves_the_queue() {
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Delete),
                          static_cast<int>(voice::actionFor(voice::UploadResult::Sent)));
    }

    void test_a_network_failure_keeps_the_recording_queued() {
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Keep),
                          static_cast<int>(voice::actionFor(voice::UploadResult::Retry)));
    }

    void test_a_refused_recording_is_parked_rather_than_deleted() {
        // A 4xx is the bridge's reading of the audio, and a bug in the bridge produces
        // one just as easily as a real fault does. The recording cannot be rebuilt, so
        // it outlives the refusal and the queue moves on without it.
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Park),
                          static_cast<int>(voice::actionFor(voice::UploadResult::Rejected)));
    }

    void test_a_drive_upload_that_landed_leaves_the_queue() {
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Delete),
                          static_cast<int>(voice::actionFor(voice::DriveResult::Sent)));
    }

    void test_a_rejected_drive_token_keeps_the_recording() {
        // 401/403 do Drive fala do token, nunca da gravação. Estacionar diria
        // que a nota é ruim; ela fica na fila e sobe quando o token for
        // corrigido.
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Keep),
                          static_cast<int>(voice::actionFor(voice::DriveResult::Unauthorized)));
    }

    void test_no_internet_keeps_the_recording() {
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Keep),
                          static_cast<int>(voice::actionFor(voice::DriveResult::NoInternet)));
    }

    void test_a_transient_drive_failure_keeps_the_recording() {
        TEST_ASSERT_EQUAL(static_cast<int>(voice::QueueAction::Keep),
                          static_cast<int>(voice::actionFor(voice::DriveResult::Retry)));
    }

    // --- o que é lixo ------------------------------------------------------

    void test_a_sidecar_without_its_recording_is_swept() {
        const auto plan = planOf({"20260909-140300.json"});
        TEST_ASSERT_EQUAL(0, plan.pending.size());
        TEST_ASSERT_TRUE(sweeps(plan, "/voice/20260909-140300.json"));
    }

    void test_a_leftover_temporary_is_swept() {
        // An atomic write that died mid-flight leaves this behind.
        const auto plan = planOf({"20260909-140300.json.tmp"});
        TEST_ASSERT_EQUAL(0, plan.pending.size());
        TEST_ASSERT_TRUE(sweeps(plan, "/voice/20260909-140300.json.tmp"));
    }

    void test_an_unrelated_file_is_left_alone() {
        // The queue directory is not ours to tidy beyond our own leftovers.
        const auto plan = planOf({"leiame.txt"});
        TEST_ASSERT_EQUAL(0, plan.pending.size());
        TEST_ASSERT_EQUAL(0, plan.sweep.size());
    }

    void test_case_does_not_decide_whether_a_file_is_a_recording() {
        // FAT is case insensitive, so a card touched on a PC can come back uppercased.
        const auto plan = planOf({"20260909-140300.WAV"});
        TEST_ASSERT_EQUAL(1, plan.pending.size());
    }

    // --- nomes -------------------------------------------------------------

    void test_a_recording_name_is_the_stamp_plus_the_extension() {
        TEST_ASSERT_EQUAL_STRING("20260909-140300.wav", voice::recordingName("20260909-140300").c_str());
    }

    void test_parking_appends_rather_than_replaces() {
        // Replacing the extension would make the audio unplayable by name alone.
        TEST_ASSERT_EQUAL_STRING("20260909-140300.wav.parked",
                                 voice::parkedName("20260909-140300.wav").c_str());
    }

    void test_recording_names_are_recognised_and_others_are_not() {
        TEST_ASSERT_TRUE(voice::isRecordingName("a.wav"));
        TEST_ASSERT_FALSE(voice::isRecordingName("a.wav.parked"));
        TEST_ASSERT_FALSE(voice::isRecordingName("a.json"));
        TEST_ASSERT_FALSE(voice::isRecordingName(".wav"));
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_an_empty_directory_has_nothing_to_do);
    RUN_TEST(test_a_recording_with_its_sidecar_is_pending);
    RUN_TEST(test_the_queue_is_ordered_oldest_first);
    RUN_TEST(test_a_recording_without_a_sidecar_is_still_pending);
    RUN_TEST(test_a_parked_recording_is_neither_pending_nor_swept);
    RUN_TEST(test_a_delivered_recording_leaves_the_queue);
    RUN_TEST(test_a_network_failure_keeps_the_recording_queued);
    RUN_TEST(test_a_refused_recording_is_parked_rather_than_deleted);
    RUN_TEST(test_a_drive_upload_that_landed_leaves_the_queue);
    RUN_TEST(test_a_rejected_drive_token_keeps_the_recording);
    RUN_TEST(test_no_internet_keeps_the_recording);
    RUN_TEST(test_a_transient_drive_failure_keeps_the_recording);
    RUN_TEST(test_a_sidecar_without_its_recording_is_swept);
    RUN_TEST(test_a_leftover_temporary_is_swept);
    RUN_TEST(test_an_unrelated_file_is_left_alone);
    RUN_TEST(test_case_does_not_decide_whether_a_file_is_a_recording);
    RUN_TEST(test_a_recording_name_is_the_stamp_plus_the_extension);
    RUN_TEST(test_parking_appends_rather_than_replaces);
    RUN_TEST(test_recording_names_are_recognised_and_others_are_not);
    return UNITY_END();
}
