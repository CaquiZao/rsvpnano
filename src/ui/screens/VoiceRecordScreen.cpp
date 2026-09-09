#include "ui/screens/VoiceRecordScreen.h"

#include <algorithm>
#include <cstdio>

#include <esp_log.h>

#include "ui/screens/Screens.h"

namespace screens {

    namespace {

        // Matches the 16 ms level slices the capture reports, so the trace advances
        // roughly sixty times a second and every bar is a fresh measurement.
        constexpr uint32_t kPushIntervalMs = 16;
        constexpr int16_t kMargin = 12;
        constexpr int16_t kTimeHeight = 40;
        constexpr int16_t kHintHeight = 24;

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
        layoutValid_ = false;
        dirty_ = true;
    }

    void VoiceRecordScreen::draw(ui::Context& ui, const VoiceRecordModel& model, uint32_t nowMs) {
        if (lastPushMs_ == 0 || (nowMs - lastPushMs_) >= kPushIntervalMs) {
            history_[head_] = model.level;
            head_ = (head_ + 1) % kBars;
            dirty_ = true;
            lastPushMs_ = nowMs;
        }

        ui.beginFrame(static_cast<uint8_t>(Screen::VoiceRecord));

        const int16_t width = ui.width();
        const int16_t height = ui.height();
        const int16_t innerWidth = static_cast<int16_t>(width - 2 * kMargin);

        if (!layoutValid_) {
            waveTop_ = static_cast<int16_t>(kMargin + kTimeHeight + 8);
            waveHeight_ = static_cast<int16_t>(height - waveTop_ - 2 * kMargin - kHintHeight);
            layoutValid_ = true;
            dirty_ = true;
        }

        // label() is slot-cached: it repaints only when the text changes, which for a
        // mm:ss clock is once a second.
        ui.label({kMargin, kMargin, innerWidth, kTimeHeight}, formatElapsed(model.elapsedMs), 3,
                 ui::themes::ColorRole::Accent, ui::TextAlign::Center, 1);

        // Every colour comes from the active theme. Hardcoding any of them would leave
        // one screen out of place across all 21 themes.
        const uint16_t accent = ui.color(ui::themes::ColorRole::Accent);
        const uint16_t background = ui.color(ui::themes::ColorRole::Background);

        if (dirty_ && waveHeight_ > 4 && innerWidth > static_cast<int16_t>(kBars)) {
            const int16_t barSpacing = static_cast<int16_t>(innerWidth / static_cast<int16_t>(kBars));
            const int16_t barWidth = std::max<int16_t>(2, static_cast<int16_t>(barSpacing - 2));
            const int16_t centre = static_cast<int16_t>(waveTop_ + waveHeight_ / 2);
            const auto halfMax = static_cast<uint8_t>(std::min<int16_t>(waveHeight_ / 2, 255));

            // The whole band is redrawn so every bar shifts left together. The previous
            // version repainted only the bar that changed: measurably cheaper, and it
            // looked frozen, because 47 of 48 bars never moved. Measured frame time is
            // about 0.03 ms, so the cheap version was solving a problem that does not
            // exist on this panel.
            ui.gfx().fillRect(kMargin, waveTop_, innerWidth, waveHeight_, background);

            for (size_t index = 0; index < kBars; ++index) {
                // Oldest on the left: the trace scrolls the way text reads.
                const size_t slot = (head_ + index) % kBars;
                const int16_t half = waveformBarHeight(history_[slot], halfMax);
                const int16_t x = static_cast<int16_t>(kMargin + static_cast<int16_t>(index) * barSpacing);
                const int16_t barHeight = static_cast<int16_t>(half * 2);
                // Radius capped at half the shorter side, so a quiet bar stays a dot
                // rather than turning inside out.
                const int16_t radius = std::min<int16_t>(barWidth / 2, barHeight / 2);
                if (radius > 0) {
                    ui.gfx().fillRoundRect(x, static_cast<int16_t>(centre - half), barWidth, barHeight, radius,
                                           accent);
                } else {
                    ui.gfx().fillRect(x, static_cast<int16_t>(centre - half), barWidth, barHeight, accent);
                }
            }
            ui.markDrawn();
            dirty_ = false;
        }

        const int16_t hintTop = static_cast<int16_t>(height - kMargin - kHintHeight);
        const char* hint = model.error != nullptr ? model.error
            : model.armed                         ? "Duplo clique para parar"
                                                  : "Preparando...";
        ui.label({kMargin, hintTop, innerWidth, kHintHeight}, hint, 2,
                 model.error != nullptr ? ui::themes::ColorRole::Accent : ui::themes::ColorRole::Muted,
                 ui::TextAlign::Center, 1);

        ui.endFrame();

        // One line every two seconds, so a laggy screen reports its own cadence
        // instead of being diagnosed by guesswork. The gap between frames is the
        // number that matters here, not how long a single draw takes.
        ++frames_;
        if (lastFrameMs_ != 0) {
            slowestFrameMs_ = std::max(slowestFrameMs_, nowMs - lastFrameMs_);
        }
        lastFrameMs_ = nowMs;
        if (nowMs - lastReportMs_ >= 2000) {
            ESP_LOGI("voice", "record screen: %u frames in 2 s, longest gap %u ms",
                     static_cast<unsigned>(frames_), static_cast<unsigned>(slowestFrameMs_));
            frames_ = 0;
            slowestFrameMs_ = 0;
            lastReportMs_ = nowMs;
        }
    }

} // namespace screens
