#pragma once

#include <cstdint>

namespace voice {

    struct CaptureResult {
        bool ok = false;
        uint32_t durationMs = 0;
        uint32_t framesWritten = 0;
        const char* error = nullptr;
    };

    // Records from the board microphone straight into a WAV on the SD card.
    // Blocking: intended for a dedicated task, never for the UI loop.
    // micGain is the capture chip gain step (3 dB each); 8 is 24 dB.
    // micPair picks which analog input pair the microphones hang off: 0 or 1.
    CaptureResult captureToFile(const char* path, uint32_t maxDurationMs, uint8_t micGain = 8,
                                uint8_t micPair = 0);

} // namespace voice
