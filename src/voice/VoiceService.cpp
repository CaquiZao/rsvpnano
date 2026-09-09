#include "voice/VoiceService.h"

#include <esp_log.h>

#include "board/BoardStorage.h"
#include <WiFi.h>

#include "network/WifiConnection.h"
#include "voice/Clock.h"
#include "voice/VoiceQueue.h"
#include "voice/VoiceUploader.h"

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        constexpr uint32_t kStackWords = 6144;
        // Priority 1: above idle so uploads make progress, below the UI so they never
        // cost a frame.
        constexpr UBaseType_t kPriority = 1;
        // A periodic sweep catches notes recorded while the network was down. Five
        // minutes is often enough to feel automatic and rare enough not to matter to
        // the battery, since an empty queue never wakes the radio.
        constexpr TickType_t kIdleTicks = pdMS_TO_TICKS(5UL * 60UL * 1000UL);

    } // namespace

    bool Service::begin() {
        if (wake_ != nullptr) {
            return true;
        }
        wake_ = xSemaphoreCreateBinary();
        lock_ = xSemaphoreCreateMutex();
        if (wake_ == nullptr || lock_ == nullptr) {
            ESP_LOGE(kTag, "could not create voice service primitives");
            return false;
        }

        queue::ensureDir(Board::Storage::filesystem());
        pendingCount_ = queue::pendingCount(Board::Storage::filesystem());

        if (xTaskCreate(&Service::taskEntry, "voice-up", kStackWords, this, kPriority, nullptr) != pdPASS) {
            ESP_LOGE(kTag, "could not start the voice upload task");
            return false;
        }
        if (pendingCount_ > 0) {
            // Something survived the last power cycle; try it as soon as we are up.
            requestFlush();
        }
        return true;
    }

    void Service::requestFlush() {
        if (wake_ != nullptr) {
            xSemaphoreGive(wake_);
        }
    }

    void Service::setCredentials(const Credentials& credentials) {
        if (lock_ == nullptr) {
            credentials_ = credentials;
            return;
        }
        xSemaphoreTake(lock_, portMAX_DELAY);
        credentials_ = credentials;
        xSemaphoreGive(lock_);
    }

    size_t Service::pendingCount() const {
        return pendingCount_;
    }

    bool Service::busy() const {
        return busy_;
    }

    const char* Service::lastError() const {
        return lastError_;
    }

    void Service::taskEntry(void* self) {
        static_cast<Service*>(self)->run();
    }

    void Service::run() {
        for (;;) {
            xSemaphoreTake(wake_, kIdleTicks);
            flushOnce();
        }
    }

    void Service::flushOnce() {
        auto& fs = Board::Storage::filesystem();
        auto items = queue::pending(fs);
        pendingCount_ = items.size();
        if (items.empty()) {
            return;
        }

        Credentials credentials;
        xSemaphoreTake(lock_, portMAX_DELAY);
        credentials = credentials_;
        xSemaphoreGive(lock_);

        if (credentials.ssid.empty()) {
            lastError_ = "Wi-Fi nao configurado";
            return;
        }

        busy_ = true;
        if (auto connected = net::connectStation(credentials.ssid.c_str(), credentials.password.c_str());
            !connected) {
            net::disconnect();
            busy_ = false;
            lastError_ = "Wi-Fi nao conectou";
            ESP_LOGW(kTag, "flush aborted: %s", connected.error().message().c_str());
            return;
        }

        ESP_LOGI(kTag, "associated with \"%s\" as %s", WiFi.SSID().c_str(),
                 WiFi.localIP().toString().c_str());

        // The clock is only worth setting while the radio is already up, and a note
        // recorded before the first sync still gets a real timestamp on the next one.
        beginTimeSync();

        const auto endpoint = discoverBridge();
        if (!endpoint) {
            net::disconnect();
            busy_ = false;
            lastError_ = "Bridge nao esta na rede";
            return;
        }

        size_t sent = 0;
        for (const auto& entry : items) {
            const auto result = upload(fs, *endpoint, entry);
            if (result == UploadResult::Retry) {
                // Stop at the first network failure rather than hammering the rest: the
                // problem is the link, not this note.
                lastError_ = "Envio falhou";
                break;
            }
            // Sent and Rejected both leave the queue. Re-sending what the bridge
            // refused would loop forever.
            queue::markSent(fs, entry);
            ++sent;
        }

        net::disconnect();
        pendingCount_ = queue::pendingCount(fs);
        busy_ = false;
        if (sent == items.size()) {
            lastError_ = nullptr;
        }
        ESP_LOGI(kTag, "flush sent %u of %u, %u still queued", static_cast<unsigned>(sent),
                 static_cast<unsigned>(items.size()), static_cast<unsigned>(pendingCount_));
    }

} // namespace voice
