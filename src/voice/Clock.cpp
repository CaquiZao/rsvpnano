#include "voice/Clock.h"

#include <Arduino.h>
#include <esp_log.h>
#include <time.h>

#include <cstdio>

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        // Any year before this means the clock was never set. 2020 is arbitrary but
        // safely past every plausible RTC default and safely before today.
        constexpr int kFirstPlausibleYear = 2020;

        bool localNow(std::tm& out) {
            const std::time_t now = std::time(nullptr);
            if (localtime_r(&now, &out) == nullptr) {
                return false;
            }
            return (out.tm_year + 1900) >= kFirstPlausibleYear;
        }

    } // namespace

    void beginTimeSync(const char* timezone) {
        // Three servers because the first can be unreachable on a captive network and
        // an unset clock costs every note its timestamp.
        configTzTime(timezone, "pool.ntp.org", "time.nist.gov", "a.st1.ntp.br");
        ESP_LOGI(kTag, "SNTP requested, tz=%s", timezone);
    }

    bool clockSynced() {
        std::tm parts = {};
        return localNow(parts);
    }

    std::string nowIso8601() {
        std::tm parts = {};
        if (!localNow(parts)) {
            return {};
        }
        char buffer[32] = {};
        std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02dT%02d:%02d:%02d", parts.tm_year + 1900,
                      parts.tm_mon + 1, parts.tm_mday, parts.tm_hour, parts.tm_min, parts.tm_sec);
        return buffer;
    }

    std::string compactStamp(uint32_t bootMs) {
        std::tm parts = {};
        char buffer[32] = {};
        if (localNow(parts)) {
            std::snprintf(buffer, sizeof(buffer), "%04d%02d%02d-%02d%02d%02d", parts.tm_year + 1900,
                          parts.tm_mon + 1, parts.tm_mday, parts.tm_hour, parts.tm_min, parts.tm_sec);
            return buffer;
        }
        // "boot-" sorts before every four-digit year, so unstamped recordings queue
        // ahead of stamped ones rather than jumbling in among them.
        std::snprintf(buffer, sizeof(buffer), "boot-%08lu", static_cast<unsigned long>(bootMs));
        return buffer;
    }

} // namespace voice
