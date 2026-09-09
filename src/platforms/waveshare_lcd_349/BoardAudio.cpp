#include "platforms/common/Es8311BoardAudio.h"

#include <Wire.h>

#include "board/BoardAudio.h"
#include "drivers/audio/es7210/Es7210.h"
#include "drivers/audio/es8311/Es8311.h"
#include "platforms/waveshare_lcd_349/WaveshareLcd349.h"

namespace {

    BoardDrivers::Es8311::Context gAudioContext = {
        Wire1,
        WaveshareLcd349::AudioWiring::kEs8311Address,
        I2S_NUM_0,
        WaveshareLcd349::AudioWiring::kMclkPin,
        WaveshareLcd349::AudioWiring::kBclkPin,
        WaveshareLcd349::AudioWiring::kWsPin,
        WaveshareLcd349::AudioWiring::kDoutPin,
        16000,
        WaveshareLcd349::AudioWiring::kDinPin,
    };

    // The microphones on this board are wired to an ES7210 capture chip, not to the
    // ES8311. The two share MCLK, BCLK and LRCK; only the data lines differ.
    BoardDrivers::Es7210::Context gMicContext = {Wire1};

} // namespace

namespace Board::Audio {

    bool begin() {
        return BoardPlatform::Es8311BoardAudio::begin(gAudioContext);
    }

    bool beep() {
        return BoardPlatform::Es8311BoardAudio::beep(gAudioContext);
    }

    bool playTone(uint32_t frequencyHz, uint32_t durationMs) {
        return BoardPlatform::Es8311BoardAudio::playTone(gAudioContext, frequencyHz, durationMs);
    }

    bool available() {
        return BoardPlatform::Es8311BoardAudio::available(gAudioContext);
    }

    bool prepareInput(uint8_t micGain, uint8_t micPair) {
        if (!BoardPlatform::Es8311BoardAudio::prepareInputBus(gAudioContext)) {
            return false;
        }
        const auto pair =
            micPair == 0 ? BoardDrivers::Es7210::MicPair::Mic12 : BoardDrivers::Es7210::MicPair::Mic34;
        return BoardDrivers::Es7210::prepareInput(gMicContext, micGain, pair);
    }

    void dumpAudioRegisters() {
        BoardDrivers::Es8311::dumpRegisters(gAudioContext);
        BoardDrivers::Es7210::dumpRegisters(gMicContext);
    }

    void scanI2cBus() {
        BoardDrivers::Es7210::scanBus(Wire1);
    }

    size_t readSamples(int16_t* samples, size_t sampleCount, uint32_t timeoutMs) {
        return BoardPlatform::Es8311BoardAudio::readSamples(gAudioContext, samples, sampleCount, timeoutMs);
    }

    bool inputAvailable() {
        return WaveshareLcd349::AudioWiring::kDinPin >= 0 && BoardDrivers::Es7210::available(gMicContext);
    }

} // namespace Board::Audio
