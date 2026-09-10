#pragma once

#include <FS.h>

#include <cstdint>
#include <optional>
#include <string>

#include "voice/VoiceQueuePlan.h"

namespace voice {

    struct Endpoint {
        std::string host;
        uint16_t port = 0;
    };

    // Reads /config/bridge.txt: "host" or "host:port". Tried before discovery, but
    // only kept if it answers: an address written down on one network is wrong on the
    // next one, and a device carried between homes must not be stranded by it.
    std::optional<Endpoint> configuredBridge(fs::FS& fs);

    // Opens a socket and closes it. Cheap enough to run before every flush, and the
    // only way to tell a stale configured address from a live one.
    bool bridgeReachable(const Endpoint& endpoint, uint32_t timeoutMs = 1500);

    // Looks for _handybridge._tcp on the local network. Needs Wi-Fi associated. The
    // service announces itself so there is no address to configure anywhere.
    std::optional<Endpoint> discoverBridge(uint32_t timeoutMs = 3000);

    enum class UploadResult : uint8_t {
        Sent,     // the bridge has the audio; drop it from the queue
        Retry,    // network or 5xx; keep it and try later
        Rejected, // 4xx; the bridge will never accept this, so stop asking
    };

    UploadResult upload(fs::FS& fs, const Endpoint& endpoint, const QueueEntry& entry);

} // namespace voice
