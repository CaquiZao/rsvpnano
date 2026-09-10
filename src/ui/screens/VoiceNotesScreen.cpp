#include "ui/screens/VoiceNotesScreen.h"

#include <algorithm>
#include <climits>
#include <cstdio>
#include <cstdlib>

#include "ui/screens/ScreenCommon.h"

namespace screens {

    namespace {

        constexpr int16_t kHeaderHeight = 26;
        constexpr int16_t kButtonHeight = 30;
        constexpr int16_t kGap = 4;
        constexpr int16_t kRowStep = 26;
        constexpr int16_t kDragThreshold = 5;
        constexpr int32_t kMaximumVelocity = 400'000;
        constexpr uint32_t kAccelerationMs = 450;
        constexpr int32_t kScrollScale = 1'000'000;

        bool allDigits(std::string_view text) {
            return std::all_of(text.begin(), text.end(), [](char c) { return c >= '0' && c <= '9'; });
        }

    } // namespace

    std::string labelFromRecordingName(std::string_view name) {
        // Names are "YYYYMMDD-HHMMSS.wav" with a clock, "boot-NNNNNNNN.wav" without.
        const size_t dot = name.find_last_of('.');
        const std::string_view stem = dot == std::string_view::npos ? name : name.substr(0, dot);

        if (stem.size() >= 5 && stem.substr(0, 5) == "boot-") {
            return "sem relogio";
        }
        const size_t dash = stem.find('-');
        if (dash != 8 || stem.size() < 15 || !allDigits(stem.substr(0, 8)) || !allDigits(stem.substr(9, 6))) {
            // Anything else came from somewhere we do not control; show it verbatim
            // rather than inventing a date for it.
            return std::string(stem);
        }
        std::string out;
        out += stem.substr(6, 2); // day
        out += '/';
        out += stem.substr(4, 2); // month
        out += ' ';
        out += stem.substr(9, 2); // hour
        out += ':';
        out += stem.substr(11, 2); // minute
        return out;
    }

    std::string formatDuration(uint32_t durationMs) {
        const uint32_t seconds = (durationMs + 500U) / 1000U;
        char buffer[16] = {};
        if (seconds < 60U) {
            std::snprintf(buffer, sizeof(buffer), "%lus", static_cast<unsigned long>(seconds));
        } else {
            std::snprintf(buffer, sizeof(buffer), "%lum%02lus", static_cast<unsigned long>(seconds / 60U),
                          static_cast<unsigned long>(seconds % 60U));
        }
        return buffer;
    }

    ui::Rect listViewport(const ui::Rect& content) {
        const int16_t buttonsTop = static_cast<int16_t>(content.y + content.h - kButtonHeight);
        const int16_t top = static_cast<int16_t>(content.y + kHeaderHeight + kGap);
        // Clamped at zero: on a panel too short for both, the buttons win and the list
        // gets nothing. A negative height would be handed straight to fillRect.
        const int16_t height = static_cast<int16_t>(std::max(0, buttonsTop - top - kGap));
        return {content.x, top, content.w, height};
    }

    Action VoiceNotesScreen::draw(ui::Context& ui, VoiceNotesModel& model, uint32_t nowMs, Screen& screen) {
        const ui::Rect content = detail::content(ui);

        // A changed list invalidates any scroll position built against the old one.
        if (rowCount_ != model.rows.size()) {
            rowCount_ = model.rows.size();
            offset_ = 0;
            dragging_ = false;
            velocity_ = 0;
            scrollRemainder_ = 0;
        }

        char position[32] = {};
        std::snprintf(position, sizeof(position), "%u / %u",
                      static_cast<unsigned>(model.rows.empty() ? 0 : model.selected + 1),
                      static_cast<unsigned>(model.rows.size()));
        ui.label({content.x, content.y, static_cast<int16_t>(content.w - 90), kHeaderHeight},
                 model.error != nullptr ? model.error : "Notas de voz", 2,
                 model.error != nullptr ? ui::themes::ColorRole::Accent : ui::themes::ColorRole::Foreground,
                 ui::TextAlign::Start);
        ui.label({static_cast<int16_t>(content.x + content.w - 90), content.y, 90, kHeaderHeight}, position, 1,
                 ui::themes::ColorRole::Muted, ui::TextAlign::Right);

        // The controls own the bottom strip; the list gets what is left. On a 172 px
        // panel that ordering is the difference between four notes and one.
        const int16_t buttonsTop = static_cast<int16_t>(content.y + content.h - kButtonHeight);
        const ui::Rect viewport = listViewport(content);

        const ui::Touch* touch = ui.touch();
        if (!model.rows.empty() && viewport.h >= kRowStep) {
            if (touch != nullptr && ui::hasTouch(*touch, ui::TouchStart)
                && ui::contains(viewport, touch->x, touch->y)) {
                dragging_ = true;
                dragStartIndex_ = model.selected;
                lastY_ = touch->y;
                dragDistance_ = 0;
                lastTickMs_ = nowMs;
                velocity_ = 0;
                scrollRemainder_ = 0;
            }
            if (dragging_ && touch != nullptr && ui::hasTouch(*touch, ui::TouchMove)) {
                const int16_t delta = static_cast<int16_t>(touch->y) - static_cast<int16_t>(lastY_);
                dragDistance_ = static_cast<uint16_t>(std::min<int>(UINT16_MAX, dragDistance_ + std::abs(delta)));
                lastY_ = touch->y;
            }

            // A tap that never turned into a drag picks the row under the finger.
            if (dragging_ && touch != nullptr && ui::hasTouch(*touch, ui::TouchRelease)
                && ui::hasTouch(*touch, ui::TouchTap) && ui::contains(viewport, touch->x, touch->y)) {
                dragging_ = false;
                offset_ = 0;
                velocity_ = 0;
                scrollRemainder_ = 0;
                model.selected = dragStartIndex_;

                const int16_t centreY = static_cast<int16_t>(viewport.y + viewport.h / 2);
                const int rows = viewport.h / kRowStep;
                const size_t half = static_cast<size_t>(std::max(1, rows / 2));
                const size_t first = model.selected > half ? model.selected - half : 0;
                const size_t last = std::min(model.rows.size(), model.selected + half + 1);
                size_t tapped = model.selected;
                int closest = INT_MAX;
                for (size_t index = first; index < last; ++index) {
                    const int rowY =
                        centreY + (static_cast<int>(index) - static_cast<int>(model.selected)) * kRowStep;
                    const int distance = std::abs(rowY - touch->y);
                    if (distance < closest) {
                        closest = distance;
                        tapped = index;
                    }
                }
                model.selected = tapped;
            }

            if (dragging_) {
                const uint32_t elapsed = std::min<uint32_t>(nowMs - lastTickMs_, 100);
                lastTickMs_ = nowMs;
                const int32_t dragRate =
                    ui::centeredDragRate(lastY_, viewport.y, viewport.h, kRowStep / 2, kMaximumVelocity);
                if (dragDistance_ > kDragThreshold && dragRate != 0) {
                    // Finger below the middle scrolls forward, above it scrolls back:
                    // the same gesture the chapter list already teaches.
                    const int32_t target = -dragRate;
                    velocity_ +=
                        static_cast<int32_t>(static_cast<int64_t>(target - velocity_) * elapsed / kAccelerationMs);
                    scrollRemainder_ += static_cast<int32_t>(static_cast<int64_t>(velocity_) * elapsed);
                    offset_ = static_cast<int16_t>(offset_ + scrollRemainder_ / kScrollScale);
                    scrollRemainder_ %= kScrollScale;
                } else {
                    velocity_ = 0;
                    scrollRemainder_ = 0;
                    if (dragDistance_ > kDragThreshold) {
                        offset_ = 0;
                    }
                }
            }

            while (offset_ <= -kRowStep / 2 && model.selected + 1 < model.rows.size()) {
                ++model.selected;
                offset_ = static_cast<int16_t>(offset_ + kRowStep);
            }
            while (offset_ >= kRowStep / 2 && model.selected > 0) {
                --model.selected;
                offset_ = static_cast<int16_t>(offset_ - kRowStep);
            }
            if (model.selected == 0) {
                offset_ = std::min<int16_t>(offset_, 0);
            }
            if (model.selected + 1 == model.rows.size()) {
                offset_ = std::max<int16_t>(offset_, 0);
            }

            if (dragging_ && touch != nullptr && ui::hasTouch(*touch, ui::TouchRelease)) {
                dragging_ = false;
                offset_ = 0;
                velocity_ = 0;
                scrollRemainder_ = 0;
            }
        }

        // One redraw guard over the whole scrolling area. Slot widgets must not live in
        // here: mixing them with raw drawing is what left the buttons half painted.
        uint32_t state = ui::Context::combine(static_cast<uint32_t>(model.rows.size()), model.selected);
        state = ui::Context::combine(state, static_cast<uint16_t>(offset_));
        state = ui::Context::combine(state, model.playing ? 1U : 0U);
        for (const auto& row : model.rows) {
            state = ui::Context::signature(row.label, ui::Context::combine(state, 0));
        }

        if (ui.redraw(viewport, state)) {
            Arduino_GFX& gfx = ui.gfx();
            gfx.fillRect(viewport.x, viewport.y, viewport.w, viewport.h,
                         ui.color(ui::themes::ColorRole::Background));

            if (model.rows.empty()) {
                // An empty screen with no words reads as a fault. Say it is empty.
                ui.drawText(viewport, "Nenhuma nota pendente. Duplo clique no BOOT lendo.", 2,
                            ui.color(ui::themes::ColorRole::Muted), ui::TextAlign::Center, 2);
            } else {
                const int16_t centreY = static_cast<int16_t>(viewport.y + viewport.h / 2);
                const int rows = std::max(1, viewport.h / kRowStep);
                const size_t half = static_cast<size_t>(rows / 2 + 1);
                const size_t first = model.selected > half ? model.selected - half : 0;
                const size_t last = std::min(model.rows.size(), model.selected + half + 1);

                for (size_t index = first; index < last; ++index) {
                    const int16_t y = static_cast<int16_t>(
                        centreY + (static_cast<int>(index) - static_cast<int>(model.selected)) * kRowStep + offset_);
                    const int16_t top = static_cast<int16_t>(y - kRowStep / 2);
                    if (top < viewport.y || top + kRowStep > viewport.y + viewport.h) {
                        continue;
                    }
                    const bool current = index == model.selected;
                    const ui::Rect rect{static_cast<int16_t>(viewport.x + 4), top,
                                        static_cast<int16_t>(viewport.w - 8),
                                        static_cast<int16_t>(kRowStep - 2)};
                    gfx.fillRoundRect(rect.x, rect.y, rect.w, rect.h, 5,
                                      ui.color(current ? ui::themes::ColorRole::SurfaceActive
                                                       : ui::themes::ColorRole::SurfaceMuted));
                    ui.drawText({static_cast<int16_t>(rect.x + 8), rect.y,
                                 static_cast<int16_t>(rect.w - 100), rect.h},
                                model.rows[index].label, 2,
                                ui.color(current ? ui::themes::ColorRole::Foreground
                                                 : ui::themes::ColorRole::Muted),
                                ui::TextAlign::Start);
                    ui.drawText({static_cast<int16_t>(rect.x + rect.w - 96), rect.y, 88, rect.h},
                                model.rows[index].detail, 1, ui.color(ui::themes::ColorRole::Muted),
                                ui::TextAlign::Right);
                }
            }
        }

        ui::Row buttons{{content.x, buttonsTop, content.w, kButtonHeight}, kGap, 0};
        const int16_t buttonWidth = static_cast<int16_t>((content.w - 2 * kGap) / 3);

        Action result = Action::None;
        const bool hasRows = !model.rows.empty();
        if (ui.button(buttons.next(buttonWidth), model.playing ? "Parar" : "Ouvir", hasRows, ui::Icon::None, 2)
            && hasRows) {
            result = model.playing ? Action::VoiceStopPlayback : Action::VoicePlay;
        }
        // Disabled while a flush runs: a second request would only queue behind the
        // first, and the button would be claiming to have done something it did not.
        if (ui.button(buttons.next(buttonWidth), model.busy ? "Enviando" : "Enviar", hasRows && !model.busy,
                      ui::Icon::None, 2)
            && hasRows && !model.busy) {
            result = Action::VoiceFlush;
        }
        if (ui.button(buttons.next(buttonWidth), "Voltar", true, ui::Icon::None, 2)) {
            screen = Screen::Device;
        }
        return result;
    }

} // namespace screens
