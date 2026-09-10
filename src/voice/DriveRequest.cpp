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

        // Percent-encodes everything application/x-www-form-urlencoded doesn't treat
        // as itself. Client secrets and refresh tokens are base64-derived and can
        // contain '+' (and '/', '='); a compliant decoder reads an unescaped '+' as a
        // space, which would silently corrupt the value in transit -- the same
        // undiagnosable auth failure a missing field produces, just by a different
        // mechanism.
        std::string urlEncode(std::string_view value) {
            static constexpr char kHex[] = "0123456789ABCDEF";
            std::string out;
            out.reserve(value.size());
            for (const char raw : value) {
                const auto byte = static_cast<unsigned char>(raw);
                const bool unreserved = (byte >= 'A' && byte <= 'Z') || (byte >= 'a' && byte <= 'z')
                    || (byte >= '0' && byte <= '9') || byte == '-' || byte == '_' || byte == '.' || byte == '~';
                if (unreserved) {
                    out += raw;
                    continue;
                }
                out += '%';
                out += kHex[byte >> 4];
                out += kHex[byte & 0x0f];
            }
            return out;
        }

    } // namespace

    bool parseDriveConfig(std::string_view toml, DriveCredentials& out) {
        // Glaze's TOML reader only writes fields present in the input; it never
        // clears the destination. Without this reset, a field left over from an
        // earlier successful parse into the same `out` would survive a later,
        // incomplete parse and let the emptiness check below pass by accident.
        out = DriveCredentials{};
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
        // application/x-www-form-urlencoded. The four values are tokens Google
        // itself issued, but "Google-issued" doesn't mean "safe unescaped" -- see
        // urlEncode's comment -- so each is percent-encoded before concatenation.
        std::string out;
        out.reserve(credentials.clientId.size() + credentials.clientSecret.size()
                    + credentials.refreshToken.size() + 64);
        out += "client_id=";
        out += urlEncode(credentials.clientId);
        out += "&client_secret=";
        out += urlEncode(credentials.clientSecret);
        out += "&refresh_token=";
        out += urlEncode(credentials.refreshToken);
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

    DriveResult driveResultForStatus(int status) {
        if (status >= 200 && status < 300)
            return DriveResult::Sent;
        switch (status) {
        case 400: // requisição que o Drive não vai aceitar como está
        case 401: // token
        case 403: // permissão
        case 404: // folder_id errado ou apagado
            return DriveResult::Unauthorized;
        default:
            return DriveResult::Retry;
        }
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
