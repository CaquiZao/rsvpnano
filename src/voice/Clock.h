#pragma once

#include <cstdint>
#include <string>

namespace voice {

    // POSIX TZ string. Brazil dropped daylight saving in 2019, so a fixed -3 offset is
    // correct and needs no rules table.
    constexpr char kDefaultTimezone[] = "<-03>3";

    // Fires off an SNTP request and returns immediately. Needs Wi-Fi already
    // associated; without it the request simply never lands and the clock stays unset.
    void beginTimeSync(const char* timezone = kDefaultTimezone);

    // A device that never synced reports 1970. The test is deliberately coarse: the
    // only error worth telling apart is "the clock is fiction".
    bool clockSynced();

    // "2026-09-09T14:03:00", or empty when the clock never synced.
    std::string nowIso8601();

    // A name that sorts by time: "20260909-140300" with a clock, "boot-00012345"
    // without one. Recordings taken before the first sync still order among
    // themselves, which is the most that can be honestly claimed.
    std::string compactStamp(uint32_t bootMs);

} // namespace voice
