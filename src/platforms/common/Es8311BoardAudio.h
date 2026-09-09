#pragma once

#include <Arduino.h>

#include <array>
#include <vector>
#include <esp_log.h>
#include "board/BoardPower.h"
#include "drivers/audio/es8311/Es8311.h"

namespace {

    constexpr char kAudioTag[] = "audio";
    constexpr uint32_t kAudioStartupDelayMs = 15;
    constexpr uint32_t kBeepFrequencyHz = 1320;
    constexpr int16_t kBeepAmplitude = 12000;
    constexpr uint32_t kEnvelopeAttackMs = 6;
    constexpr uint32_t kEnvelopeReleaseMs = 12;
    constexpr uint32_t kSampleRateHz = 16000;
    constexpr uint32_t kBeepDurationMs = 120;
    constexpr uint32_t kWriteTimeoutMs = 250;
    // What startCodec() leaves in register 0x32.
    constexpr uint8_t kDacFullVolume = 0xFF;
    constexpr size_t kBeepFrames = (static_cast<size_t>(kSampleRateHz) * kBeepDurationMs) / 1000U;
    constexpr size_t kBeepSamples = kBeepFrames * 2U;

    constexpr std::array<int16_t, kBeepSamples> makeBeepBuffer() {
        std::array<int16_t, kBeepSamples> buffer{};
        const size_t attackFrames = (static_cast<size_t>(kSampleRateHz) * kEnvelopeAttackMs) / 1000U;
        const size_t releaseFrames = (static_cast<size_t>(kSampleRateHz) * kEnvelopeReleaseMs) / 1000U;
        const uint32_t halfPeriodSamples = kSampleRateHz / (kBeepFrequencyHz * 2U);

        for (size_t frame = 0; frame < kBeepFrames; ++frame) {
            int32_t sample = ((frame / halfPeriodSamples) % 2U == 0U) ? kBeepAmplitude : -kBeepAmplitude;

            if (attackFrames > 0 && frame < attackFrames) {
                sample = (sample * static_cast<int32_t>(frame)) / static_cast<int32_t>(attackFrames);
            } else if (releaseFrames > 0 && frame >= (kBeepFrames - releaseFrames)) {
                const size_t remaining = kBeepFrames - frame;
                sample = (sample * static_cast<int32_t>(remaining)) / static_cast<int32_t>(releaseFrames);
            }

            const size_t index = frame * 2U;
            buffer[index] = static_cast<int16_t>(sample);
            buffer[index + 1U] = static_cast<int16_t>(sample);
        }
        return buffer;
    }

    constexpr auto kBeepBuffer = makeBeepBuffer();

    bool enableAudioRail() {
        return Board::Power::enableAudioPowerIfAvailable();
    }

    bool writeBeepBuffer(BoardDrivers::Es8311::Context& context) {
        return BoardDrivers::Es8311::writeSamples(context, kBeepBuffer.data(), kBeepBuffer.size(), kWriteTimeoutMs);
    }

} // namespace

namespace BoardPlatform::Es8311BoardAudio {

    bool begin(BoardDrivers::Es8311::Context& context) {
        if (BoardDrivers::Es8311::available(context)) {
            return true;
        }

        if (!enableAudioRail()) {
            ESP_LOGW(kAudioTag, "Audio rail unavailable");
            return false;
        }

        delay(kAudioStartupDelayMs);
        return BoardDrivers::Es8311::begin(context);
    }

    // Rendered into a heap buffer rather than the constexpr one, because the tones
    // differ in pitch and length at runtime. 200 ms of stereo 16 kHz is 12.8 KB.
    bool playTone(BoardDrivers::Es8311::Context& context, uint32_t frequencyHz, uint32_t durationMs,
                  int16_t amplitude = kBeepAmplitude, uint8_t volume = kDacFullVolume) {
        if (!enableAudioRail() || !BoardDrivers::Es8311::prepareOutput(context)) {
            return false;
        }
        if (frequencyHz == 0 || durationMs == 0) {
            return false;
        }

        const size_t frames = (static_cast<size_t>(kSampleRateHz) * durationMs) / 1000U;
        const uint32_t halfPeriod = kSampleRateHz / (frequencyHz * 2U);
        if (frames == 0 || halfPeriod == 0) {
            return false;
        }

        std::vector<int16_t> buffer(frames * 2U);
        const size_t attackFrames = (static_cast<size_t>(kSampleRateHz) * kEnvelopeAttackMs) / 1000U;
        const size_t releaseFrames = (static_cast<size_t>(kSampleRateHz) * kEnvelopeReleaseMs) / 1000U;
        for (size_t frame = 0; frame < frames; ++frame) {
            int32_t sample = ((frame / halfPeriod) % 2U == 0U) ? amplitude : -amplitude;
            // The same attack and release as the beep. Without them a tone clicks, and
            // a click on every recording start would be worse than no feedback.
            if (attackFrames > 0 && frame < attackFrames) {
                sample = (sample * static_cast<int32_t>(frame)) / static_cast<int32_t>(attackFrames);
            } else if (releaseFrames > 0 && frame + releaseFrames >= frames) {
                const size_t remaining = frames - frame;
                sample = (sample * static_cast<int32_t>(remaining)) / static_cast<int32_t>(releaseFrames);
            }
            buffer[frame * 2U] = static_cast<int16_t>(sample);
            buffer[frame * 2U + 1U] = static_cast<int16_t>(sample);
        }

        // Bring the DAC down for the tone and put it back afterwards, so the focus
        // timer beep keeps the loudness it always had.
        BoardDrivers::Es8311::setOutputVolume(context, volume);
        const bool written = BoardDrivers::Es8311::writeSamples(context, buffer.data(), buffer.size(),
                                                                kWriteTimeoutMs + durationMs);
        BoardDrivers::Es8311::setOutputVolume(context, kDacFullVolume);
        return written;
    }

    bool beep(BoardDrivers::Es8311::Context& context) {
        if (!enableAudioRail() || !BoardDrivers::Es8311::prepareOutput(context)) {
            return false;
        }

        if (writeBeepBuffer(context)) {
            return true;
        }

        ESP_LOGW(kAudioTag, "Retrying speaker beep after recovering output path");
        if (!enableAudioRail() || !BoardDrivers::Es8311::recoverOutputPath(context)) {
            return false;
        }

        return writeBeepBuffer(context);
    }

    // Only powers the rail and the shared I2S peripheral. Boards whose microphones
    // hang off a separate capture chip configure that chip themselves.
    bool prepareInputBus(BoardDrivers::Es8311::Context& context) {
        if (!enableAudioRail()) {
            ESP_LOGW(kAudioTag, "Audio rail unavailable");
            return false;
        }
        delay(kAudioStartupDelayMs);
        return BoardDrivers::Es8311::prepareInputBus(context);
    }

    bool prepareInput(BoardDrivers::Es8311::Context& context, bool useDmic = false) {
        if (!enableAudioRail()) {
            ESP_LOGW(kAudioTag, "Audio rail unavailable");
            return false;
        }
        delay(kAudioStartupDelayMs);
        return BoardDrivers::Es8311::prepareInput(context, useDmic);
    }

    size_t readSamples(BoardDrivers::Es8311::Context& context, int16_t* samples, size_t sampleCount,
                       uint32_t timeoutMs) {
        return BoardDrivers::Es8311::readSamples(context, samples, sampleCount, timeoutMs);
    }

    bool available(const BoardDrivers::Es8311::Context& context) {
        return BoardDrivers::Es8311::available(context);
    }

} // namespace BoardPlatform::Es8311BoardAudio
