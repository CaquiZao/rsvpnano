#include "platforms/common/Es8311BoardAudio.h"

#include <Wire.h>

#include "board/BoardAudio.h"
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

} // namespace

namespace Board::Audio {

    bool begin() {
        return BoardPlatform::Es8311BoardAudio::begin(gAudioContext);
    }

    bool beep() {
        return BoardPlatform::Es8311BoardAudio::beep(gAudioContext);
    }

    bool available() {
        return BoardPlatform::Es8311BoardAudio::available(gAudioContext);
    }

    bool prepareInput(bool useDmic) {
        return BoardPlatform::Es8311BoardAudio::prepareInput(gAudioContext, useDmic);
    }

    void dumpAudioRegisters() {
        BoardDrivers::Es8311::dumpRegisters(gAudioContext);
    }

    size_t readSamples(int16_t* samples, size_t sampleCount, uint32_t timeoutMs) {
        return BoardPlatform::Es8311BoardAudio::readSamples(gAudioContext, samples, sampleCount, timeoutMs);
    }

    bool inputAvailable() {
        return WaveshareLcd349::AudioWiring::kDinPin >= 0
            && BoardPlatform::Es8311BoardAudio::available(gAudioContext);
    }

} // namespace Board::Audio
