#include "voice/DriveUploader.h"

#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <esp_log.h>

#include <algorithm>
#include <vector>

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        constexpr char kDriveConfigPath[] = "/config/drive.toml";
        constexpr size_t kMaxConfigBytes = 2048;

        constexpr char kTokenUrl[] = "https://oauth2.googleapis.com/token";
        constexpr char kUploadHost[] = "www.googleapis.com";
        constexpr char kUploadPath[] = "/upload/drive/v3/files?uploadType=multipart";
        constexpr uint16_t kHttpsPort = 443;

        constexpr uint32_t kConnectTimeoutMs = 4000;
        // Token exchange and the small JSON Drive answers with should both be quick;
        // ten seconds is for a slow radio, not for either endpoint doing real work.
        constexpr uint32_t kResponseTimeoutMs = 10000;
        constexpr uint32_t kHandshakeTimeoutS = 15;
        constexpr size_t kChunkBytes = 4096;

        // Google Trust Services' GTS Root R1: the self-signed root that
        // oauth2.googleapis.com's and www.googleapis.com's certificate chains lead
        // to (currently through an intermediate Google calls WR2, but the
        // intermediate is Google's to rotate without notice -- pinning the root is
        // what survives that). Fetched from https://pki.goog/repo/certs/gtsr1.pem on
        // 2026-09-10; sha256 fingerprint D9:47:43:2A:BD:E7:B7:FA:90:FC:2E:6B:59:10:
        // 1B:12:80:E0:E1:C7:E4:E4:0F:A3:C6:88:7F:FF:57:A7:F4:CF. Valid 2016-06-22
        // through 2036-06-22 -- check https://pki.goog/repository/ well before then.
        constexpr char kGoogleRootCa[] = R"(-----BEGIN CERTIFICATE-----
MIIFVzCCAz+gAwIBAgINAgPlk28xsBNJiGuiFzANBgkqhkiG9w0BAQwFADBHMQsw
CQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEU
MBIGA1UEAxMLR1RTIFJvb3QgUjEwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAw
MDAwWjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZp
Y2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjEwggIiMA0GCSqGSIb3DQEBAQUA
A4ICDwAwggIKAoICAQC2EQKLHuOhd5s73L+UPreVp0A8of2C+X0yBoJx9vaMf/vo
27xqLpeXo4xL+Sv2sfnOhB2x+cWX3u+58qPpvBKJXqeqUqv4IyfLpLGcY9vXmX7w
Cl7raKb0xlpHDU0QM+NOsROjyBhsS+z8CZDfnWQpJSMHobTSPS5g4M/SCYe7zUjw
TcLCeoiKu7rPWRnWr4+wB7CeMfGCwcDfLqZtbBkOtdh+JhpFAz2weaSUKK0Pfybl
qAj+lug8aJRT7oM6iCsVlgmy4HqMLnXWnOunVmSPlk9orj2XwoSPwLxAwAtcvfaH
szVsrBhQf4TgTM2S0yDpM7xSma8ytSmzJSq0SPly4cpk9+aCEI3oncKKiPo4Zor8
Y/kB+Xj9e1x3+naH+uzfsQ55lVe0vSbv1gHR6xYKu44LtcXFilWr06zqkUspzBmk
MiVOKvFlRNACzqrOSbTqn3yDsEB750Orp2yjj32JgfpMpf/VjsPOS+C12LOORc92
wO1AK/1TD7Cn1TsNsYqiA94xrcx36m97PtbfkSIS5r762DL8EGMUUXLeXdYWk70p
aDPvOmbsB4om3xPXV2V4J95eSRQAogB/mqghtqmxlbCluQ0WEdrHbEg8QOB+DVrN
VjzRlwW5y0vtOUucxD/SVRNuJLDWcfr0wbrM7Rv1/oFB2ACYPTrIrnqYNxgFlQID
AQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4E
FgQU5K8rJnEaK0gnhS9SZizv8IkTcT4wDQYJKoZIhvcNAQEMBQADggIBAJ+qQibb
C5u+/x6Wki4+omVKapi6Ist9wTrYggoGxval3sBOh2Z5ofmmWJyq+bXmYOfg6LEe
QkEzCzc9zolwFcq1JKjPa7XSQCGYzyI0zzvFIoTgxQ6KfF2I5DUkzps+GlQebtuy
h6f88/qBVRRiClmpIgUxPoLW7ttXNLwzldMXG+gnoot7TiYaelpkttGsN/H9oPM4
7HLwEXWdyzRSjeZ2axfG34arJ45JK3VmgRAhpuo+9K4l/3wV3s6MJT/KYnAK9y8J
ZgfIPxz88NtFMN9iiMG1D53Dn0reWVlHxYciNuaCp+0KueIHoI17eko8cdLiA6Ef
MgfdG+RCzgwARWGAtQsgWSl4vflVy2PFPEz0tv/bal8xa5meLMFrUKTX5hgUvYU/
Z6tGn6D/Qqc6f1zLXbBwHSs09dR2CQzreExZBfMzQsNhFRAbd03OIozUhfJFfbdT
6u9AWpQKXCBfTkBdYiJ23//OYb2MI3jSNwLgjt7RETeJ9r/tSQdirpLsQBqvFAnZ
0E6yove+7u7Y/9waLd64NnHi/Hm3lCXRSHNboTXns5lndcEZOitHTtNCjv0xyBZm
2tIMPNuzjsmhDYAPexZ3FL//2wmUspO8IFgV6dtxQ/PeEMMA3KgqlbbC1j+Qa3bb
bP6MvPJwNQzcmRk13NfIRmPVNnGuV/u3gm3c
-----END CERTIFICATE-----)";

        std::string fileNameOf(const std::string& path) {
            const size_t slash = path.find_last_of('/');
            return slash == std::string::npos ? path : path.substr(slash + 1);
        }

        // Reads the status line only, same as VoiceUploader.cpp's readStatusCode and
        // for the same reason: Drive's upload response body is a JSON object nothing
        // downstream needs, and draining it would only cost time on the radio.
        int readStatusCode(WiFiClientSecure& client) {
            const uint32_t deadline = millis() + kResponseTimeoutMs;
            while (client.connected() && !client.available()) {
                if (static_cast<int32_t>(millis() - deadline) >= 0) {
                    return -1;
                }
                delay(5);
            }
            const String line = client.readStringUntil('\n');
            const int firstSpace = line.indexOf(' ');
            if (firstSpace < 0) {
                return -1;
            }
            return line.substring(firstSpace + 1, firstSpace + 4).toInt();
        }

        // Trades the long-lived refresh token for a short-lived access token. Uses
        // HTTPClient rather than a raw socket, unlike the file uploads below: the
        // response is a few hundred bytes of JSON that has to be read (not just its
        // status line), and HTTPClient's framing (Content-Length or chunked, either
        // is legal here) is one less thing to get wrong for a body this small.
        //
        // Returns the token on success. On failure, sets `failure` to why -- NEVER
        // logs the token itself, only ever the outcome, the same care taken in
        // "fix(bridge): não deixar o token do Telegram cair no log".
        std::optional<std::string> refreshAccessToken(const DriveCredentials& credentials, DriveResult& failure) {
            WiFiClientSecure client;
            client.setCACert(kGoogleRootCa);
            client.setHandshakeTimeout(kHandshakeTimeoutS);

            HTTPClient http;
            http.setConnectTimeout(static_cast<int32_t>(kConnectTimeoutMs));
            http.setTimeout(static_cast<uint16_t>(kResponseTimeoutMs));
            if (!http.begin(client, kTokenUrl)) {
                ESP_LOGW(kTag, "token request could not be prepared");
                failure = DriveResult::NoInternet;
                return std::nullopt;
            }
            http.addHeader("Content-Type", "application/x-www-form-urlencoded");

            const std::string body = refreshBody(credentials);
            const int status = http.POST(String(body.c_str()));

            if (status < 0) {
                // A negative return is HTTPClient's own error code. Most of them mean
                // the connection never became a request-response exchange at all
                // (refused, no route, TLS handshake failed) -- that is NoInternet.
                // CONNECTION_LOST and READ_TIMEOUT are different: both only happen
                // after a connection did open, so Retry is the honest answer -- and
                // it matters here specifically, because a handshake failure from a
                // rotated GTS Root R1 or an active MITM must not be reported as
                // "no internet", which would send the user to check their router for
                // a problem that is not there.
                ESP_LOGW(kTag, "token refresh could not connect: %s (%d)", HTTPClient::errorToString(status).c_str(),
                         status);
                http.end();
                failure = (status == HTTPC_ERROR_CONNECTION_LOST || status == HTTPC_ERROR_READ_TIMEOUT)
                    ? DriveResult::Retry
                    : DriveResult::NoInternet;
                return std::nullopt;
            }
            if (status == 400 || status == 401 || status == 403) {
                // Google's token endpoint answers a stale or revoked refresh token
                // with 400 (invalid_grant, invalid_client...), not 401 -- 401/403
                // are covered too in case that ever changes.
                ESP_LOGW(kTag, "token refresh rejected with %d", status);
                http.end();
                failure = DriveResult::Unauthorized;
                return std::nullopt;
            }
            if (status != 200) {
                ESP_LOGW(kTag, "token refresh got %d", status);
                http.end();
                failure = DriveResult::Retry;
                return std::nullopt;
            }

            const String responseBody = http.getString();
            http.end();

            std::string token;
            if (!parseAccessToken(std::string_view(responseBody.c_str(), responseBody.length()), token)) {
                ESP_LOGW(kTag, "token response had no usable access_token");
                failure = DriveResult::Unauthorized;
                return std::nullopt;
            }
            ESP_LOGI(kTag, "refreshed Drive access token");
            return token;
        }

        // Uploads one file as a Drive multipart/related object. Mirrors
        // VoiceUploader::upload's structure exactly: a raw TLS socket (so the exact
        // Content-Length can be sent before the first body byte), the body written
        // as header/content/footer, the content streamed off the card in 4 KB heap
        // chunks, and only the response status line read back.
        DriveResult uploadFile(fs::FS& fs, const std::string& accessToken, const std::string& path,
                               std::string_view contentType, const std::string& folderId) {
            File file = fs.open(path.c_str());
            if (!file) {
                // Queued but gone from the card by the time we got here. DriveResult
                // has no "give up on this one" outcome -- see actionFor(DriveResult)
                // in VoiceQueuePlan.cpp, which never parks a Drive failure -- so
                // Retry is the closest honest answer. If the file really is gone,
                // every future attempt lands right back here for the cost of a log
                // line, not a lost note misclassified as sent.
                ESP_LOGW(kTag, "cannot open %s for Drive upload", path.c_str());
                return DriveResult::Retry;
            }
            const size_t contentBytes = file.size();
            const std::string filename = fileNameOf(path);
            const std::string metadata = uploadMetadata(filename, folderId);
            const std::string header = uploadHeader(kUploadBoundary, metadata, contentType);
            const std::string footer = uploadFooter(kUploadBoundary);
            const size_t total = header.size() + contentBytes + footer.size();

            WiFiClientSecure client;
            client.setCACert(kGoogleRootCa);
            client.setHandshakeTimeout(kHandshakeTimeoutS);
            // No client.setTimeout() here: it takes milliseconds, and
            // kConnectTimeoutMs / 1000 (copied from VoiceUploader.cpp, which passes
            // its own kConnectTimeoutMs / 1000 to the same call) would set it to 4 ms,
            // not 4 s -- readStringUntil could then return a partial status line and
            // manufacture a spurious Retry. readStatusCode already has its own
            // deadline below, so nothing here needs a second one.
            if (!client.connect(kUploadHost, kHttpsPort, kConnectTimeoutMs)) {
                file.close();
                ESP_LOGW(kTag, "could not reach Drive to upload %s", filename.c_str());
                return DriveResult::NoInternet;
            }

            String request = "POST ";
            request += kUploadPath;
            request += " HTTP/1.1\r\nHost: ";
            request += kUploadHost;
            request += "\r\nAuthorization: Bearer ";
            request += accessToken.c_str();
            request += "\r\nContent-Type: multipart/related; boundary=";
            request += kUploadBoundary;
            request += "\r\nContent-Length: ";
            request += String(static_cast<uint32_t>(total));
            request += "\r\nConnection: close\r\n\r\n";
            // `request` carries the bearer token. It is written to the socket, never
            // to the log.
            client.print(request);
            client.write(reinterpret_cast<const uint8_t*>(header.data()), header.size());

            // Streamed in 4 KB chunks, on the heap -- same reasoning as
            // VoiceUploader::upload: a ten-minute WAV is 19 MB, far more than this
            // device should hold in RAM at once, and 4 KB of stack in a task already
            // deep in lwIP/mbedTLS is what panicked the board before.
            std::vector<uint8_t> chunk(kChunkBytes);
            size_t sent = 0;
            while (sent < contentBytes) {
                // Clamped to what is left: an unclamped read could put up to one
                // full chunk more on the wire than Content-Length promised if the
                // file grew after `contentBytes` was captured, and Drive would then
                // have a complete, valid body before the mismatch is ever noticed.
                const size_t toRead = std::min(chunk.size(), contentBytes - sent);
                const size_t read = file.read(chunk.data(), toRead);
                if (read == 0) {
                    break;
                }
                if (client.write(chunk.data(), read) != read) {
                    file.close();
                    client.stop();
                    ESP_LOGW(kTag, "upload of %s cut short at %u bytes", filename.c_str(),
                             static_cast<unsigned>(sent));
                    return DriveResult::Retry;
                }
                sent += read;
            }
            file.close();

            if (sent != contentBytes) {
                // Content-Length promised bytes the card could not produce. Closing
                // without finishing the body is the only honest move; Drive discards
                // it.
                client.stop();
                ESP_LOGW(kTag, "%s read short: %u of %u", filename.c_str(), static_cast<unsigned>(sent),
                         static_cast<unsigned>(contentBytes));
                return DriveResult::Retry;
            }

            client.write(reinterpret_cast<const uint8_t*>(footer.data()), footer.size());

            const int status = readStatusCode(client);
            client.stop();

            if (status >= 200 && status < 300) {
                ESP_LOGI(kTag, "uploaded %s to Drive (%u bytes)", filename.c_str(),
                         static_cast<unsigned>(contentBytes));
                return DriveResult::Sent;
            }
            if (status == 401 || status == 403) {
                ESP_LOGW(kTag, "Drive rejected %s with %d", filename.c_str(), status);
                return DriveResult::Unauthorized;
            }
            ESP_LOGW(kTag, "upload of %s got %d", filename.c_str(), status);
            return DriveResult::Retry;
        }

    } // namespace

    std::optional<DriveCredentials> loadDriveConfig(fs::FS& fs) {
        File file = fs.open(kDriveConfigPath);
        if (!file) {
            return std::nullopt;
        }
        std::string text;
        text.resize(std::min<size_t>(file.size(), kMaxConfigBytes));
        file.read(reinterpret_cast<uint8_t*>(text.data()), text.size());
        file.close();

        DriveCredentials credentials;
        if (!parseDriveConfig(text, credentials)) {
            ESP_LOGW(kTag, "drive.toml missing or incomplete");
            return std::nullopt;
        }
        return credentials;
    }

    DriveResult uploadToDrive(fs::FS& fs, const DriveCredentials& credentials, const QueueEntry& entry) {
        DriveResult failure = DriveResult::NoInternet;
        const std::optional<std::string> accessToken = refreshAccessToken(credentials, failure);
        if (!accessToken) {
            return failure;
        }

        const DriveResult wavResult = uploadFile(fs, *accessToken, entry.wavPath, "audio/wav", credentials.folderId);
        if (wavResult != DriveResult::Sent) {
            return wavResult;
        }

        if (entry.metaPath.empty()) {
            // No sidecar was ever written for this note. drive_inbox.py's grace
            // period already tolerates a permanently sidecar-less pair once it
            // elapses -- the same tolerance the bridge's direct-upload path has for
            // a missing sidecar today -- so there is nothing left to send.
            return DriveResult::Sent;
        }

        // The sidecar goes up only after the WAV is confirmed. drive_inbox.py reads
        // a lone .wav as an upload still in flight and only treats it as
        // sidecar-less for good after a 5-minute grace period; a sidecar with no
        // .wav behind it is not a case that module expects at all.
        const DriveResult metaResult =
            uploadFile(fs, *accessToken, entry.metaPath, "application/json", credentials.folderId);
        if (metaResult != DriveResult::Sent) {
            // The WAV is already on Drive without its sidecar. Retry unconditionally
            // here, regardless of the sidecar's own status: the incomplete pair
            // simply waits out the bridge's grace period, and this attempt or the
            // next still has a chance to complete it. Anything else risks the note
            // being read as sent when its anchor never arrived.
            ESP_LOGW(kTag, "sidecar for %s did not make it after the WAV did", fileNameOf(entry.wavPath).c_str());
            return DriveResult::Retry;
        }
        return DriveResult::Sent;
    }

} // namespace voice
