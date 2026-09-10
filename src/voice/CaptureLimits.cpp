#include "voice/CaptureLimits.h"

namespace voice {

    CaptureStop shouldStopCapture(uint8_t batteryPercent, uint64_t freeBytes) {
        // Battery first: it is the one the reader can do something about, and a board
        // that browns out mid-write leaves a WAV with no length in its header.
        if (batteryPercent > 0 && batteryPercent < kMinimumBatteryPercent) {
            return CaptureStop::Battery;
        }
        if (freeBytes < kMinimumFreeBytes) {
            return CaptureStop::Disk;
        }
        return CaptureStop::None;
    }

} // namespace voice
