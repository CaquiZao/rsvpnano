#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace voice {

    // Everything the bridge needs to turn a WAV into a note. Written next to the
    // recording as a JSON sidecar so a recording that survives a power cut still
    // knows where in which book it was spoken.
    struct NoteMeta {
        // ISO 8601 local time. Empty when the clock never synced.
        std::string recordedAt;
        bool clockSynced = false;
        // Book file name without directory or extension. Empty outside the reader.
        std::string book;
        uint32_t wordOffset = 0;
        // The paragraph being read when recording started.
        std::string excerpt;
        uint32_t durationMs = 0;
    };

    // Empty fields are omitted, never sent as "". The bridge tells a loose note from a
    // reader note by the absence of the key, so an empty string would be a lie.
    std::string toJson(const NoteMeta& meta);

    // Same path with the extension swapped for .json.
    std::string sidecarPath(std::string_view wavPath);

    // Escapes what JSON requires and nothing else: accented UTF-8 passes through as
    // bytes, because the excerpt is Portuguese and \u escapes would only obscure it.
    std::string escapeJsonString(std::string_view text);

} // namespace voice
