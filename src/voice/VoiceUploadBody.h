#pragma once

#include <cstddef>
#include <string>
#include <string_view>

namespace voice {

    // The multipart form the bridge's POST /v1/notes expects: a "meta" field carrying
    // the sidecar JSON and an "audio" field carrying the WAV. Built as three pieces so
    // the body can be streamed from the card instead of assembled in RAM -- a ten
    // minute note is 19 MB, and the PSRAM has other jobs.
    std::string multipartHeader(std::string_view boundary, std::string_view filename,
                                std::string_view metaJson);
    std::string multipartFooter(std::string_view boundary);

    // Content-Length has to be exact before the first byte goes out.
    size_t multipartLength(std::string_view boundary, std::string_view filename, std::string_view metaJson,
                           size_t audioBytes);

    // Fixed rather than random: this runs over a LAN to one known service, and a
    // constant makes a failing upload reproducible from a packet capture.
    constexpr char kBoundary[] = "rsvpnanoVoiceNoteBoundary";

} // namespace voice
