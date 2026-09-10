#pragma once

#include <cstdint>

namespace voice {

    enum class CaptureStop : uint8_t {
        None = 0,
        Battery,
        Disk,
    };

    // A recording has no fixed length, so something else has to end it when the reader
    // forgets. Checked periodically during capture rather than per block: reading the
    // gauge and the card costs more than writing the audio does.
    //
    // `batteryPercent` of zero means the board has no gauge to read; that must not end
    // a recording, because losing a note to a sensor that was never there is worse than
    // the flat battery this guards against.
    CaptureStop shouldStopCapture(uint8_t batteryPercent, uint64_t freeBytes);

    // Below this the device is close to shutting itself down, and a WAV cut off by a
    // power loss has no header length written.
    constexpr uint8_t kMinimumBatteryPercent = 15;

    // A minute of 16 kHz mono plus room for the sidecar and the filesystem's own
    // bookkeeping. Stopping with this much left keeps the card mountable.
    constexpr uint64_t kMinimumFreeBytes = 4ULL * 1024ULL * 1024ULL;

} // namespace voice
