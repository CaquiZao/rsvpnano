#include "voice/Clock.h"

#include <Arduino.h>
#include <Preferences.h>
#include <esp_log.h>
#include <esp_random.h>
#include <time.h>

#include <cstdio>

#include "voice/VoiceQueuePlan.h"

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        // Any year before this means the clock was never set. 2020 is arbitrary but
        // safely past every plausible RTC default and safely before today.
        constexpr int kFirstPlausibleYear = 2020;

        // Counts power cycles, in NVS, so a recording taken before the clock syncs still
        // has a name nothing else will ever reuse.
        //
        // This is not bookkeeping: the bridge deduplicates notes by the file stem, so two
        // recordings sharing one stem cost a recording. `millis()` restarts at 0 on every
        // boot, and pre-sync recordings are exactly the ones that sit in the queue across
        // a reboot, so the collision was reachable rather than theoretical.
        //
        // Read and incremented once per boot, then cached: the sequence must not change
        // between two recordings of the same session.
        uint32_t bootSequence() {
            static uint32_t cached = 0;
            if (cached != 0) {
                return cached;
            }
            Preferences prefs;
            if (!prefs.begin("voice", false)) {
                // A random value still beats a counter that restarts: it keeps two boots
                // from colliding, it just cannot order them.
                cached = (esp_random() % 0xFFFEU) + 1U;
                ESP_LOGW(kTag, "NVS unavailable; boot sequence is random for this boot");
                return cached;
            }
            cached = prefs.getUInt("boot_seq", 0) + 1U;
            prefs.putUInt("boot_seq", cached);
            prefs.end();
            ESP_LOGI(kTag, "boot sequence %lu", static_cast<unsigned long>(cached));
            return cached;
        }

        bool localNow(tm& out) {
            const time_t now = time(nullptr);
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
        tm parts = {};
        return localNow(parts);
    }

    std::string nowIso8601() {
        tm parts = {};
        if (!localNow(parts)) {
            return {};
        }
        char buffer[32] = {};
        snprintf(buffer, sizeof(buffer), "%04d-%02d-%02dT%02d:%02d:%02d", parts.tm_year + 1900,
                      parts.tm_mon + 1, parts.tm_mday, parts.tm_hour, parts.tm_min, parts.tm_sec);
        return buffer;
    }

    std::string compactStamp(uint32_t bootMs) {
        tm parts = {};
        char buffer[32] = {};
        if (localNow(parts)) {
            snprintf(buffer, sizeof(buffer), "%04d%02d%02d-%02d%02d%02d", parts.tm_year + 1900,
                          parts.tm_mon + 1, parts.tm_mday, parts.tm_hour, parts.tm_min, parts.tm_sec);
            return buffer;
        }
        // Every unstamped name starts with "boot-", so they stay in one block instead of
        // jumbling in among the stamped ones. They sort *after* those, not before: 'b'
        // comes after every digit in ASCII, which the comment here used to claim
        // backwards.
        return bootStamp(bootSequence(), bootMs);
    }

} // namespace voice
