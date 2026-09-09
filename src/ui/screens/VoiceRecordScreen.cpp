#include "ui/screens/VoiceRecordScreen.h"

#include <algorithm>
#include <cstdio>

#include "ui/screens/Screens.h"

namespace screens {

    namespace {

        // One bar every 100 ms, independent of the frame rate, so the waveform scrolls
        // at the same speed whatever else the reader is doing.
        constexpr uint32_t kPushIntervalMs = 100;
        constexpr int16_t kMargin = 12;

    } // namespace

    uint8_t waveformBarHeight(uint8_t level, uint8_t maxHeight) {
        if (maxHeight == 0) {
            return 0;
        }
        // Silence still draws one pixel. A row of nothing reads as a frozen screen,
        // and the whole point of this screen is to show the device is listening.
        const uint32_t span = maxHeight > 1 ? static_cast<uint32_t>(maxHeight - 1) : 0;
        const uint32_t scaled = (static_cast<uint32_t>(level) * span + 127U) / 255U;
        return static_cast<uint8_t>(1U + scaled);
    }

    std::string formatElapsed(uint32_t elapsedMs) {
        const uint32_t totalSeconds = elapsedMs / 1000U;
        char buffer[16] = {};
        // Minutes unpadded and uncapped: recording has no length limit, so an hour-long
        // note must read as 61:02 rather than wrap to 1:02.
        std::snprintf(buffer, sizeof(buffer), "%lu:%02lu", static_cast<unsigned long>(totalSeconds / 60U),
                      static_cast<unsigned long>(totalSeconds % 60U));
        return buffer;
    }

    void VoiceRecordScreen::reset() {
        history_.fill(0);
        head_ = 0;
        lastPushMs_ = 0;
    }

    void VoiceRecordScreen::draw(ui::Context& ui, const VoiceRecordModel& model, uint32_t nowMs) {
        if (lastPushMs_ == 0 || (nowMs - lastPushMs_) >= kPushIntervalMs) {
            history_[head_] = model.level;
            head_ = (head_ + 1) % kBars;
            lastPushMs_ = nowMs;
        }

        ui.beginFrame(static_cast<uint8_t>(Screen::VoiceRecord));

        const int16_t width = ui.width();
        const int16_t height = ui.height();
        const int16_t innerWidth = static_cast<int16_t>(width - 2 * kMargin);

        // Every colour comes from the active theme. Hardcoding any of them would leave
        // one screen out of place across all 21 themes.
        const uint16_t accent = ui.color(ui::themes::ColorRole::Accent);
        const uint16_t muted = ui.color(ui::themes::ColorRole::Muted);
        const uint16_t background = ui.color(ui::themes::ColorRole::Background);

        ui.gfx().fillRect(0, 0, width, height, background);

        const int16_t timeHeight = 40;
        ui.label({kMargin, kMargin, innerWidth, timeHeight}, formatElapsed(model.elapsedMs), 3,
                 ui::themes::ColorRole::Accent, ui::TextAlign::Center, 1);

        // The waveform gets the middle band, the widest thing on screen.
        const int16_t waveTop = static_cast<int16_t>(kMargin + timeHeight + 8);
        const int16_t waveHeight = static_cast<int16_t>(height - waveTop - 2 * kMargin - 24);
        if (waveHeight > 4 && innerWidth > static_cast<int16_t>(kBars)) {
            const int16_t barSpacing = static_cast<int16_t>(innerWidth / static_cast<int16_t>(kBars));
            const int16_t barWidth = std::max<int16_t>(1, static_cast<int16_t>(barSpacing - 1));
            const int16_t centre = static_cast<int16_t>(waveTop + waveHeight / 2);
            const auto halfMax = static_cast<uint8_t>(std::min<int16_t>(waveHeight / 2, 255));

            for (size_t index = 0; index < kBars; ++index) {
                // Oldest bar on the left: the trace scrolls the way text reads.
                const size_t slot = (head_ + index) % kBars;
                const uint8_t half = waveformBarHeight(history_[slot], halfMax);
                const int16_t x = static_cast<int16_t>(kMargin + static_cast<int16_t>(index) * barSpacing);
                ui.gfx().fillRect(x, static_cast<int16_t>(centre - half), barWidth,
                                  static_cast<int16_t>(half * 2), accent);
            }
        }

        const int16_t hintTop = static_cast<int16_t>(height - kMargin - 24);
        const char* hint = model.error != nullptr ? model.error
            : model.armed                         ? "Duplo clique para parar"
                                                  : "Preparando...";
        ui.label({kMargin, hintTop, innerWidth, 24}, hint, 2,
                 model.error != nullptr ? ui::themes::ColorRole::Accent : ui::themes::ColorRole::Muted,
                 ui::TextAlign::Center, 1);
        static_cast<void>(muted);

        ui.endFrame();
    }

} // namespace screens
