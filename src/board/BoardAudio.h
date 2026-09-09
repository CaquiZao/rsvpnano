#pragma once

#include <Arduino.h>

namespace Board::Audio {

    bool begin();
    bool beep();
    // A single square-wave tone. amplitude is the peak sample value out of 32767;
    // the recording cues are deliberately faint because they fire in a classroom.
    bool playTone(uint32_t frequencyHz, uint32_t durationMs, int16_t amplitude = 12000,
                  uint8_t dacVolume = 0xFF);
    bool available();

    // Capture is implemented only on boards wired for it. Declaring it here does not
    // oblige the other platforms: nothing in their builds calls these.
    // micGain is the capture chip's own gain step (3 dB each). 8 is 24 dB.
    // micPair picks which analog input pair the microphones hang off: 0 or 1.
    bool prepareInput(uint8_t micGain = 8, uint8_t micPair = 0);
    void dumpAudioRegisters();
    // Logs every I2C device that answers. Diagnostic only.
    void scanI2cBus();
    size_t readSamples(int16_t* samples, size_t sampleCount, uint32_t timeoutMs);
    // Stereo interleaved, 16 kHz. Used to play a recorded note back.
    bool writeSamples(const int16_t* samples, size_t sampleCount, uint32_t timeoutMs);
    bool inputAvailable();

} // namespace Board::Audio
