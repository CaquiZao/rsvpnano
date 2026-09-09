#pragma once

#include <array>
#include <cstdint>
#include <string>

#include "ui/Ui.h"

namespace screens {

    struct VoiceRecordModel {
        uint32_t elapsedMs = 0;
        uint8_t level = 0; // block RMS, 0..255
        bool armed = false;
        const char* error = nullptr;
    };

    // Maps a level to a bar height. Split out because it is the one part of the screen
    // worth testing, and it has a decision in it: silence still draws a baseline.
    uint8_t waveformBarHeight(uint8_t level, uint8_t maxHeight);

    // "0:07", "12:34". Minutes are not padded; the recording has no length limit, so
    // an hour-long note reads as "61:02" rather than wrapping.
    std::string formatElapsed(uint32_t elapsedMs);

    // The trace scrolls: every bar shifts left on each new level. Measured frame time
    // on this panel is about 0.03 ms, so there is no reason to be clever about it -- and
    // the clever version, repainting only the bar that changed, looked frozen.
    class VoiceRecordScreen {
    public:
        void reset();
        void draw(ui::Context& ui, const VoiceRecordModel& model, uint32_t nowMs);

    private:
        static constexpr size_t kBars = 48;

        std::array<uint8_t, kBars> history_{};
        size_t head_ = 0;
        uint32_t lastPushMs_ = 0;
        bool layoutValid_ = false;
        bool dirty_ = true;
        int16_t waveTop_ = 0;
        int16_t waveHeight_ = 0;
        uint32_t frames_ = 0;
        uint32_t slowestFrameMs_ = 0;
        uint32_t lastReportMs_ = 0;
        uint32_t lastFrameMs_ = 0;
    };

} // namespace screens
