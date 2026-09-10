#include "voice/DriveRequest.h"

#include <glaze/json.hpp>
#include <glaze/toml.hpp>

#include <utility>

#include "voice/VoiceNoteMeta.h"

// The config file on the card is TOML, like every other config this firmware reads;
// snake_case keys map onto the camelCase members the rest of the codebase uses via
// glz::meta, same as SettingsModel's mapping.
template<>
struct glz::meta<voice::DriveCredentials> {
    using T = voice::DriveCredentials;
    static constexpr auto value = glz::object("client_id", &T::clientId, "client_secret", &T::clientSecret,
                                              "refresh_token", &T::refreshToken, "folder_id", &T::folderId);
};

namespace voice {

    namespace {

        // Google's token endpoint responds with JSON, not TOML -- the only place this
        // module reads JSON rather than writes it. Named to match the wire field
        // verbatim since this struct never leaves this file.
        struct TokenResponse {
            std::string access_token;
        };

    } // namespace

    bool parseDriveConfig(std::string_view toml, DriveCredentials& out) {
        const auto error =
            glz::read<glz::opts{.format = glz::TOML, .error_on_unknown_keys = false}>(out, toml);
        if (error)
            return false;
        // All four fields or none: a config missing one would still be attempted on
        // every flush and fail every time with a 401 that gives no clue why.
        return !out.clientId.empty() && !out.clientSecret.empty() && !out.refreshToken.empty()
            && !out.folderId.empty();
    }

    std::string refreshBody(const DriveCredentials& credentials) {
        // application/x-www-form-urlencoded. The four values are tokens Google itself
        // issued (OAuth client ids/secrets, base64url refresh tokens); none of them
        // can contain a byte urlencoding would need to escape, so this concatenates
        // rather than escapes.
        std::string out;
        out.reserve(credentials.clientId.size() + credentials.clientSecret.size()
                    + credentials.refreshToken.size() + 64);
        out += "client_id=";
        out += credentials.clientId;
        out += "&client_secret=";
        out += credentials.clientSecret;
        out += "&refresh_token=";
        out += credentials.refreshToken;
        out += "&grant_type=refresh_token";
        return out;
    }

    bool parseAccessToken(std::string_view json, std::string& out) {
        TokenResponse response;
        if (glz::read<glz::opts{.format = glz::JSON, .error_on_unknown_keys = false}>(response, json))
            return false;
        // An error response ({"error":"invalid_grant"}) parses fine and simply leaves
        // access_token empty; treat that the same as a malformed response rather than
        // handing the caller a token that isn't one.
        if (response.access_token.empty())
            return false;
        out = std::move(response.access_token);
        return true;
    }

    std::string uploadMetadata(std::string_view name, std::string_view folderId) {
        // Hand-built rather than through glaze: one line, one field that needs
        // escaping. The folder id is a Google-issued opaque id, never user text, so it
        // passes through unescaped like the tokens in refreshBody.
        std::string out;
        out.reserve(name.size() + folderId.size() + 32);
        out += "{\"name\":\"";
        out += escapeJsonString(name);
        out += "\",\"parents\":[\"";
        out += folderId;
        out += "\"]}";
        return out;
    }

    std::string uploadHeader(std::string_view boundary, std::string_view metadata, std::string_view contentType) {
        std::string out;
        out.reserve(metadata.size() + contentType.size() + boundary.size() * 2 + 96);
        out += "--";
        out += boundary;
        // Drive requires the metadata part to declare application/json and to come
        // before the content part -- reversing the order is a documented rejection,
        // not just bad style.
        out += "\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n";
        out += metadata;
        out += "\r\n--";
        out += boundary;
        out += "\r\nContent-Type: ";
        out += contentType;
        out += "\r\n\r\n";
        return out;
    }

    std::string uploadFooter(std::string_view boundary) {
        std::string out = "\r\n--";
        out += boundary;
        out += "--\r\n";
        return out;
    }

} // namespace voice
