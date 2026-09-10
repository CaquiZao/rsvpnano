#pragma once

#include <cstdint>
#include <functional>

#include "voice/CaptureLimits.h"

namespace voice {

    struct CaptureResult {
        bool ok = false;
        uint32_t durationMs = 0;
        uint32_t framesWritten = 0;
        const char* error = nullptr;
        // Why an unlimited recording ended by itself. None means the caller
        // asked, or the ceiling was reached.
        CaptureStop stopReason = CaptureStop::None;
    };

    struct CaptureCallbacks {
        // Called once per block, roughly twice a second. `level` is the block RMS
        // scaled to 0..255. Return false to stop the recording.
        std::function<bool(uint32_t elapsedMs, uint8_t level)> onBlock;
    };

    // Hard ceiling even when maxDurationMs says unlimited. Without one, a button
    // pressed in a pocket would record until the card filled.
    constexpr uint32_t kCaptureCeilingMs = 30UL * 60UL * 1000UL;

    // Records from the board microphone straight into a WAV on the SD card.
    // Blocking: intended for a dedicated task, never for the UI loop.
    //
    // maxDurationMs == 0 means no limit: recording ends when the callback asks, when
    // the card refuses a write, or at kCaptureCeilingMs.
    // micGain is the capture chip gain step (3 dB each); 8 is 24 dB, measured on
    // hardware to land around -21 dBFS for a normal speaking voice.
    // micPair picks which analog input pair the microphones hang off: 0 or 1.
    CaptureResult captureToFile(const char* path, uint32_t maxDurationMs, uint8_t micGain = 8,
                                uint8_t micPair = 0, const CaptureCallbacks* callbacks = nullptr);

    // Block RMS scaled to 0..255. Exposed for the level meter and for tests.
    uint8_t blockLevel(const int16_t* samples, size_t sampleCount);

} // namespace voice
