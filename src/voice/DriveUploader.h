#pragma once

#include <FS.h>

#include <optional>

#include "voice/DriveRequest.h"
#include "voice/VoiceQueuePlan.h"

namespace voice {

    // Reads /config/drive.toml (up to 2 KB) off the card and hands back credentials
    // only when every field parseDriveConfig requires is present. std::nullopt means
    // "Drive is not set up on this device," not "something went wrong" -- the caller
    // should just skip the Drive flush quietly, the way a device with no bridge
    // configured skips that flush too.
    std::optional<DriveCredentials> loadDriveConfig(fs::FS& fs);

    // Refreshes the access token over TLS, then uploads the WAV and -- only once the
    // WAV is confirmed -- the sidecar, each streamed off the card in 4 KB chunks the
    // way VoiceUploader::upload streams to the bridge. Never builds a file in RAM.
    // See DriveUploader.cpp for why the WAV and the sidecar are never reordered.
    DriveResult uploadToDrive(fs::FS& fs, const DriveCredentials& credentials, const QueueEntry& entry);

} // namespace voice
