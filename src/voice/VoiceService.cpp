#include "voice/VoiceService.h"

#include <esp_log.h>

#include "board/BoardStorage.h"
#include <WiFi.h>

#include "network/WifiConnection.h"
#include "voice/Clock.h"
#include "voice/DriveUploader.h"
#include "voice/VoiceQueue.h"
#include "voice/VoiceUploader.h"

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        // Bytes, not words: xTaskCreate is the ESP-IDF one. This task opens a socket and
        // streams a file off the card, the same shape of work as the background job,
        // so it gets the same room.
        constexpr uint32_t kStackBytes = 12288;
        // Priority 1: above idle so uploads make progress, below the UI so they never
        // cost a frame.
        constexpr UBaseType_t kPriority = 1;
        // A periodic sweep catches notes recorded while the network was down. Five
        // minutes is often enough to feel automatic and rare enough not to matter to
        // the battery, since an empty queue never wakes the radio.
        constexpr TickType_t kIdleTicks = pdMS_TO_TICKS(5UL * 60UL * 1000UL);
        // How long to let another radio user finish before giving up on this round.
        // The startup update check is the one that matters and it resolves in seconds.
        constexpr uint32_t kRadioWaitMs = 60UL * 1000UL;
        constexpr uint32_t kRadioPollMs = 500;

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

        if (xTaskCreate(&Service::taskEntry, "voice-up", kStackBytes, this, kPriority, nullptr) != pdPASS) {
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

    void Service::setRadioGate(std::function<bool()> gate) {
        radioGate_ = std::move(gate);
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
            if (waitForRadio()) {
                flushOnce();
            }
        }
    }

    // True when the radio is ours to use. False means someone else still holds it
    // and this round is skipped; the periodic tick comes back to it.
    bool Service::waitForRadio() {
        if (!radioGate_) {
            return true;
        }
        for (uint32_t waited = 0; waited < kRadioWaitMs; waited += kRadioPollMs) {
            if (!radioGate_()) {
                return true;
            }
            vTaskDelay(pdMS_TO_TICKS(kRadioPollMs));
        }
        ESP_LOGW(kTag, "radio still busy after %us; skipping this round",
                 static_cast<unsigned>(kRadioWaitMs / 1000U));
        lastError_ = "Rede ocupada";
        return false;
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

        // The written address is a hint, not a rule. It is right at home and wrong
        // everywhere else, so it is only used if something actually answers there;
        // otherwise the device finds the bridge on whatever network it is on now.
        auto endpoint = configuredBridge(fs);
        if (endpoint && !bridgeReachable(*endpoint)) {
            ESP_LOGI(kTag, "configured bridge at %s:%u did not answer; discovering",
                     endpoint->host.c_str(), static_cast<unsigned>(endpoint->port));
            endpoint.reset();
        }
        if (!endpoint) {
            endpoint = discoverBridge();
        }
        if (!endpoint) {
            // Not finding the bridge says nothing about whether the bridge is on the
            // network -- it might be, behind a firewall that only blocks inbound, and
            // reachable to anything that could open a connection to it. That used to
            // be reported as "Bridge nao esta na rede," a claim this code has no way
            // to know is true. Drive is the fallback for exactly this gap, and it
            // needs the radio that is already up, so it runs before disconnecting
            // rather than after.
            auto driveCredentials = loadDriveConfig(fs);
            if (!driveCredentials) {
                net::disconnect();
                busy_ = false;
                lastError_ = "Bridge fora da rede; Drive nao configurado";
                return;
            }

            size_t sent = 0;
            size_t refused = 0;
            for (const auto& entry : items) {
                const DriveResult result = uploadToDrive(fs, *driveCredentials, entry);
                switch (actionFor(result)) {
                case QueueAction::Keep:
                    // Same "stop at the first failure" rule as the LAN route below.
                    // A Drive result never parks (see actionFor(DriveResult) in
                    // VoiceQueuePlan.cpp), so Keep is the only branch a failure can
                    // land in; which message depends on what actually failed.
                    switch (result) {
                    case DriveResult::Unauthorized:
                        // A pasta entra aqui junto com o token: um folder_id
                        // errado ou apagado responde 404, e dizer "sem
                        // internet" mandaria o usuario olhar o roteador por um
                        // erro que esta no config.
                        lastError_ = "Drive recusou: token ou pasta";
                        break;
                    case DriveResult::NoInternet:
                    case DriveResult::Retry:
                    case DriveResult::Sent:
                        lastError_ = "Bridge fora da rede; sem internet";
                        break;
                    }
                    break;
                case QueueAction::Park:
                    // actionFor(DriveResult) never returns this today. Handled anyway
                    // so this switch stays the same shape as the LAN one below, and
                    // so a future change there cannot fall through unnoticed.
                    queue::markRejected(fs, entry);
                    ++refused;
                    continue;
                case QueueAction::Delete:
                    queue::markSent(fs, entry);
                    ++sent;
                    continue;
                }
                break;
            }

            net::disconnect();
            pendingCount_ = queue::pendingCount(fs);
            busy_ = false;
            if (sent == items.size()) {
                lastError_ = nullptr;
            }
            ESP_LOGI(kTag, "drive flush sent %u of %u, %u refused, %u still queued",
                     static_cast<unsigned>(sent), static_cast<unsigned>(items.size()),
                     static_cast<unsigned>(refused), static_cast<unsigned>(pendingCount_));
            return;
        }

        size_t sent = 0;
        size_t refused = 0;
        for (const auto& entry : items) {
            switch (actionFor(upload(fs, *endpoint, entry))) {
            case QueueAction::Keep:
                // Stop at the first network failure rather than hammering the rest: the
                // problem is the link, not this note.
                lastError_ = "Envio falhou";
                break;
            case QueueAction::Park:
                // Out of the queue so it cannot loop, but still on the card. Asking
                // again would be pointless; deleting it would be unrecoverable.
                queue::markRejected(fs, entry);
                ++refused;
                continue;
            case QueueAction::Delete:
                queue::markSent(fs, entry);
                ++sent;
                continue;
            }
            break;
        }

        net::disconnect();
        pendingCount_ = queue::pendingCount(fs);
        busy_ = false;
        if (refused > 0) {
            // A refusal used to be counted as a delivery, which cleared this and left
            // the reader told that a note the vault never received was on its way.
            lastError_ = "Bridge recusou a nota";
        } else if (sent == items.size()) {
            lastError_ = nullptr;
        }
        ESP_LOGI(kTag, "flush sent %u of %u, %u refused, %u still queued", static_cast<unsigned>(sent),
                 static_cast<unsigned>(items.size()), static_cast<unsigned>(refused),
                 static_cast<unsigned>(pendingCount_));
    }

} // namespace voice
