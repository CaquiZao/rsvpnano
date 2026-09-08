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
    CaptureResult captureToFile(const char* path, uint32_t maxDurationMs);

} // namespace voice
