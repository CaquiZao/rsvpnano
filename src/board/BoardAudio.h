#pragma once

#include <Arduino.h>

namespace Board::Audio {

    bool begin();
    bool beep();
    bool available();

    // Capture is implemented only on boards wired for it. Declaring it here does not
    // oblige the other platforms: nothing in their builds calls these.
    bool prepareInput();
    size_t readSamples(int16_t* samples, size_t sampleCount, uint32_t timeoutMs);
    bool inputAvailable();

} // namespace Board::Audio
