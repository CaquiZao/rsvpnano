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

    // O resultado de uma tentativa pelo Drive. Separado de UploadResult porque
    // os erros significam coisas diferentes: um 4xx do bridge é um juízo sobre
    // a nota, e um 401 do Drive é um problema de token que não diz nada sobre
    // ela.
    enum class DriveResult : uint8_t {
        Sent,          // 2xx: o Drive confirmou a durabilidade na linha de status
        Retry,         // 429, 5xx, ou falha de rede
        Unauthorized,  // 400/401/403/404: token, permissão ou pasta
        NoInternet,    // não deu para falar com o Google
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
    QueueAction actionFor(DriveResult result);

    // `names` are file names inside kQueueDir, without the directory.
    QueuePlan planFrom(std::span<const std::string> names);

    // Names are timestamps, so ordering them lexically orders them by time. Recordings
    // taken without a clock use a boot-relative stamp and sort among themselves.
    std::string recordingName(std::string_view stamp);

    // The stamp for a recording taken before the clock synced: a boot sequence number
    // that survives power cycles, then milliseconds since that boot.
    //
    // The sequence number is what makes the stamp an identity. `millis()` alone restarts
    // at 0 on every boot, so two different recordings could carry the same name -- and
    // the bridge deduplicates by exactly this stem, so a collision there costs a
    // recording. It also fixes the ordering: with the counter first, a recording from a
    // later boot always sorts after one from an earlier boot, which raw uptime could not
    // express.
    std::string bootStamp(uint32_t bootSeq, uint32_t bootMs);
    std::string parkedName(std::string_view wavName);

    // True for the names planFrom would treat as a recording awaiting upload.
    bool isRecordingName(std::string_view name);

} // namespace voice
