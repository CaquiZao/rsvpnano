#pragma once

#include <FS.h>

#include <vector>

#include "voice/VoiceNoteMeta.h"
#include "voice/VoiceQueuePlan.h"

namespace voice::queue {

    // Creates kQueueDir if it is missing.
    bool ensureDir(fs::FS& fs);

    // Lists the directory and applies planFrom(), sweeping the leftovers it names.
    std::vector<QueueEntry> pending(fs::FS& fs);
    size_t pendingCount(fs::FS& fs);

    // Writes the sidecar atomically: temp file then rename. The card lives in a device
    // that can lose power mid-write, and a half-written sidecar would be unparseable.
    bool writeSidecar(fs::FS& fs, const char* wavPath, const NoteMeta& meta);

    // Delivered: both files go.
    bool markSent(fs::FS& fs, const QueueEntry& entry);

    // Refused by the bridge. Both files are renamed out of the queue rather than
    // deleted: a 4xx says the bridge could not use this note, not that the recording
    // was worthless, and the two are indistinguishable from here.
    bool markRejected(fs::FS& fs, const QueueEntry& entry);

    // Failed again. Past kMaxUploadAttempts the recording is renamed out of the queue
    // rather than deleted, so one bad file cannot block every note behind it.
    bool markFailed(fs::FS& fs, const QueueEntry& entry, uint8_t attempts);

} // namespace voice::queue
