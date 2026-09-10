#include "voice/VoiceUploader.h"

#include <ESPmDNS.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <esp_log.h>

#include <array>
#include <cstdlib>
#include <vector>

#include "voice/VoiceUploadBody.h"

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        constexpr char kService[] = "handybridge";
        // What discovery.py publishes as the server name, and the port it binds.
        constexpr char kHostname[] = "handy-bridge";
        constexpr uint16_t kDefaultPort = 8787;
        // An explicit address, one line, edited over USB transfer. Routers routinely
        // forward unicast between their bands while dropping multicast, which leaves
        // mDNS unable to find a bridge the device can reach perfectly well.
        constexpr char kConfigPath[] = "/config/bridge.txt";
        constexpr char kPath[] = "/v1/notes";
        constexpr uint32_t kConnectTimeoutMs = 4000;
        // The bridge answers before transcribing, so it should reply in well under a
        // second. Ten is for a card that reads slowly, not for inference.
        constexpr uint32_t kResponseTimeoutMs = 10000;
        constexpr size_t kChunkBytes = 4096;

        std::string fileNameOf(const std::string& path) {
            const size_t slash = path.find_last_of('/');
            return slash == std::string::npos ? path : path.substr(slash + 1);
        }

        std::string readAll(fs::FS& fs, const std::string& path, size_t limit) {
            std::string out;
            if (path.empty()) {
                return out;
            }
            File file = fs.open(path.c_str());
            if (!file) {
                return out;
            }
            const size_t size = file.size() > limit ? limit : file.size();
            out.resize(size);
            file.read(reinterpret_cast<uint8_t*>(out.data()), size);
            file.close();
            return out;
        }

        // Reads the status line only. The bridge's body is a short JSON ack and nothing
        // downstream needs it, so draining it would only cost time on the radio.
        int readStatusCode(WiFiClient& client) {
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

    } // namespace

    std::optional<Endpoint> configuredBridge(fs::FS& fs) {
        File file = fs.open(kConfigPath);
        if (!file) {
            return std::nullopt;
        }
        std::string text;
        text.resize(std::min<size_t>(file.size(), 64));
        file.read(reinterpret_cast<uint8_t*>(text.data()), text.size());
        file.close();

        // "host" or "host:port", trailing whitespace tolerated because this file is
        // meant to be edited by hand over USB transfer.
        while (!text.empty()
               && (text.back() == '\n' || text.back() == '\r'
                   || text.back() == ' ' || text.back() == '\0')) {
            text.pop_back();
        }
        if (text.empty()) {
            return std::nullopt;
        }
        Endpoint endpoint{text, kDefaultPort};
        if (const size_t colon = text.find_last_of(':'); colon != std::string::npos) {
            endpoint.host = text.substr(0, colon);
            endpoint.port = static_cast<uint16_t>(atoi(text.c_str() + colon + 1));
        }
        if (endpoint.host.empty() || endpoint.port == 0) {
            return std::nullopt;
        }
        ESP_LOGI(kTag, "bridge configured at %s:%u", endpoint.host.c_str(),
                 static_cast<unsigned>(endpoint.port));
        return endpoint;
    }

    std::optional<Endpoint> discoverBridge(uint32_t timeoutMs) {
        if (WiFi.status() != WL_CONNECTED) {
            ESP_LOGW(kTag, "discovery skipped: no Wi-Fi");
            return std::nullopt;
        }
        // Idempotent enough to call again; the companion API may already have started
        // mDNS for its own service.
        if (!MDNS.begin(WiFi.getHostname())) {
            ESP_LOGW(kTag, "MDNS.begin failed");
        }

        const uint32_t deadline = millis() + timeoutMs;
        uint32_t attempt = 0;
        do {
            ++attempt;
            const int found = MDNS.queryService(kService, "tcp");
            ESP_LOGI(kTag, "query %u for _%s._tcp returned %d", static_cast<unsigned>(attempt), kService, found);
            for (int index = 0; index < found; ++index) {
                const IPAddress address = MDNS.address(index);
                const uint16_t port = MDNS.port(index);
                ESP_LOGI(kTag, "  candidate %s:%u", address.toString().c_str(), static_cast<unsigned>(port));
                if (port != 0 && address != IPAddress()) {
                    Endpoint endpoint{std::string(address.toString().c_str()), port};
                    return endpoint;
                }
            }
        } while (static_cast<int32_t>(millis() - deadline) < 0);

        // Service discovery can come back empty on networks that drop the PTR query
        // while still answering an A query. The bridge publishes itself as
        // handy-bridge.local, so ask for the host directly before giving up.
        const IPAddress host = MDNS.queryHost(kHostname, 2000);
        if (host != IPAddress()) {
            ESP_LOGI(kTag, "found %s.local at %s", kHostname, host.toString().c_str());
            return Endpoint{std::string(host.toString().c_str()), kDefaultPort};
        }

        ESP_LOGW(kTag, "no _%s._tcp and no %s.local on this network", kService, kHostname);
        return std::nullopt;
    }

    UploadResult upload(fs::FS& fs, const Endpoint& endpoint, const QueueEntry& entry) {
        File audio = fs.open(entry.wavPath.c_str());
        if (!audio) {
            // The file vanished under us. Nothing to send and nothing to retry.
            ESP_LOGW(kTag, "cannot open %s", entry.wavPath.c_str());
            return UploadResult::Rejected;
        }
        const size_t audioBytes = audio.size();

        // A missing sidecar is not fatal: the note goes out as a loose one.
        std::string meta = readAll(fs, entry.metaPath, 8192);
        if (meta.empty()) {
            meta = "{\"clock_synced\":false}";
        }

        const std::string filename = fileNameOf(entry.wavPath);
        const std::string head = multipartHeader(kBoundary, filename, meta);
        const std::string foot = multipartFooter(kBoundary);
        const size_t total = head.size() + audioBytes + foot.size();

        WiFiClient client;
        client.setTimeout(kConnectTimeoutMs / 1000);
        if (!client.connect(endpoint.host.c_str(), endpoint.port, kConnectTimeoutMs)) {
            audio.close();
            ESP_LOGW(kTag, "connect to %s:%u failed", endpoint.host.c_str(),
                     static_cast<unsigned>(endpoint.port));
            return UploadResult::Retry;
        }

        String request = "POST ";
        request += kPath;
        request += " HTTP/1.1\r\nHost: ";
        request += endpoint.host.c_str();
        request += "\r\nContent-Type: multipart/form-data; boundary=";
        request += kBoundary;
        request += "\r\nContent-Length: ";
        request += String(static_cast<uint32_t>(total));
        request += "\r\nConnection: close\r\n\r\n";
        client.print(request);
        client.write(reinterpret_cast<const uint8_t*>(head.data()), head.size());

        // Streamed in 4 KB chunks. Building the body in RAM would need 19 MB for a ten
        // minute note, and the PSRAM has other jobs.
        // On the heap on purpose: 4 KB of stack inside a task that is also down in
        // lwIP is what overflowed and panicked the board on every boot.
        std::vector<uint8_t> chunk(kChunkBytes);
        size_t sent = 0;
        while (sent < audioBytes) {
            const size_t read = audio.read(chunk.data(), chunk.size());
            if (read == 0) {
                break;
            }
            if (client.write(chunk.data(), read) != read) {
                audio.close();
                client.stop();
                ESP_LOGW(kTag, "upload of %s cut short at %u bytes", filename.c_str(),
                         static_cast<unsigned>(sent));
                return UploadResult::Retry;
            }
            sent += read;
        }
        audio.close();

        if (sent != audioBytes) {
            // Content-Length promised bytes the card could not produce. Closing without
            // finishing the body is the only honest move; the server discards it.
            client.stop();
            ESP_LOGW(kTag, "%s read short: %u of %u", filename.c_str(), static_cast<unsigned>(sent),
                     static_cast<unsigned>(audioBytes));
            return UploadResult::Retry;
        }

        client.write(reinterpret_cast<const uint8_t*>(foot.data()), foot.size());

        const int status = readStatusCode(client);
        client.stop();

        if (status >= 200 && status < 300) {
            ESP_LOGI(kTag, "sent %s (%u bytes)", filename.c_str(), static_cast<unsigned>(audioBytes));
            return UploadResult::Sent;
        }
        if (status >= 400 && status < 500) {
            // Re-sending what the bridge refused is an infinite loop.
            ESP_LOGW(kTag, "bridge rejected %s with %d", filename.c_str(), status);
            return UploadResult::Rejected;
        }
        ESP_LOGW(kTag, "upload of %s got %d", filename.c_str(), status);
        return UploadResult::Retry;
    }

} // namespace voice
