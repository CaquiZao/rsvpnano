#pragma once

#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <vector>

namespace voice {

    // A recording waiting to be sent, plus the sidecar describing it. metaPath is empty
    // when the sidecar is missing: the note still goes out, just without its anchor.
    struct QueueEntry {
        std::string wavPath;
        std::string metaPath;
    };

    // What to do with a directory listing. Deciding this away from the filesystem keeps
    // the ordering and sweeping rules under test on the host, where they belong.
    struct QueuePlan {
        std::vector<QueueEntry> pending; // oldest first
        std::vector<std::string> sweep;  // orphan sidecars and stray temporaries
    };

    constexpr char kQueueDir[] = "/voice";
    // Beyond this many failed attempts a recording is parked instead of retried. The
    // audio is never deleted -- a queue stuck behind one bad file would cost every note
    // after it.
    constexpr uint8_t kMaxUploadAttempts = 5;

    enum class UploadResult : uint8_t {
        Sent,     // the bridge has the audio
        Retry,    // network or 5xx; keep it and try later
        Rejected, // 4xx; the bridge will never accept this, so stop asking
    };

    // What the queue does with a recording after an attempt. A pure decision, and
    // deliberately here rather than inside the flush loop: the rule that a refusal
    // must not destroy audio is the kind of thing that has to stay under test on the
    // host, and inside the loop only real hardware could ever reach it.
    enum class QueueAction : uint8_t {
        Delete, // Delivered. The bridge owns it now.
        Keep,   // Still ours to retry.
        Park,   // Refused. Out of the queue, but not off the card.
    };

    QueueAction actionFor(UploadResult result);

    // `names` are file names inside kQueueDir, without the directory.
    QueuePlan planFrom(std::span<const std::string> names);

    // Names are timestamps, so ordering them lexically orders them by time. Recordings
    // taken without a clock use a boot-relative stamp and sort among themselves.
    std::string recordingName(std::string_view stamp);
    std::string parkedName(std::string_view wavName);

    // True for the names planFrom would treat as a recording awaiting upload.
    bool isRecordingName(std::string_view name);

} // namespace voice
