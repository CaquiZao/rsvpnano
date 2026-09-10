#pragma once

#include <string>
#include <string_view>

namespace voice {

    // The four secrets `/config/drive.toml` must carry for the device to speak to the
    // Drive API as the user's own app: an OAuth client (id + secret), the long-lived
    // refresh token minted once during setup, and the folder recordings land in. All
    // four or none -- see parseDriveConfig.
    struct DriveCredentials {
        std::string clientId;
        std::string clientSecret;
        std::string refreshToken;
        std::string folderId;
    };

    // Reads /config/drive.toml. Returns false, leaving `out` not to be trusted, on bad
    // TOML *or* on TOML that parses but is missing any of the four fields -- a partial
    // credential would still attempt every upload and fail every one with a 401, with
    // nothing in the log to say why.
    bool parseDriveConfig(std::string_view toml, DriveCredentials& out);

    // application/x-www-form-urlencoded body for the OAuth "refresh_token" grant, the
    // request that trades the long-lived refresh token for a short-lived access token.
    std::string refreshBody(const DriveCredentials& credentials);

    // Pulls access_token out of the token endpoint's JSON response. Returns false for
    // an error response (e.g. {"error":"invalid_grant"}) or any response without a
    // usable token, so a stale or revoked refresh token surfaces as "no token" rather
    // than an empty string quietly standing in for one.
    bool parseAccessToken(std::string_view json, std::string& out);

    // The "metadata" part of a multipart/related upload: the file's name and the
    // folder it belongs in. Built by hand rather than through glaze because it is one
    // line of JSON with a single field that needs escaping.
    std::string uploadMetadata(std::string_view name, std::string_view folderId);

    // The multipart/related envelope Drive's upload endpoint expects: metadata first,
    // raw content second. Same three-piece shape as VoiceUploadBody, for the same
    // reason -- the header can be written before the audio is known, so a recording
    // is streamed from the card rather than copied into RAM.
    std::string uploadHeader(std::string_view boundary, std::string_view metadata, std::string_view contentType);
    std::string uploadFooter(std::string_view boundary);

    // Fixed rather than random, same reasoning as VoiceUploadBody's kBoundary: this
    // string can never legitimately appear inside our own JSON metadata or a WAV
    // payload, so a constant costs nothing and keeps a failing upload reproducible.
    constexpr char kUploadBoundary[] = "rsvpnanoDriveUploadBoundary";

} // namespace voice
