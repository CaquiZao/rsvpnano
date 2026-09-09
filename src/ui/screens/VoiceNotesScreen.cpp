#include "ui/screens/VoiceNotesScreen.h"

#include <algorithm>
#include <cstdio>

#include "ui/screens/ScreenCommon.h"

namespace screens {

    namespace {

        constexpr int16_t kRowHeight = 24;
        constexpr int16_t kButtonHeight = 30;
        constexpr int16_t kGap = 4;
        // One row of three buttons plus the line that reports the last upload error.
        // Stacked, those buttons took 122 px of a 148 px content area and left room for
        // exactly one note. This panel is 640 wide and 172 tall: width is what there is
        // plenty of, so the controls go side by side.
        constexpr int16_t kReservedHeight = kButtonHeight + kGap + 18;

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

    size_t visibleRowCount(int16_t contentHeight, int16_t rowHeight, int16_t reservedHeight) {
        if (rowHeight <= 0) {
            return 0;
        }
        const int16_t forList = static_cast<int16_t>(contentHeight - reservedHeight);
        if (forList < rowHeight) {
            // The controls always win. A list that pushes the back button off the panel
            // strands the user on this screen.
            return 0;
        }
        return static_cast<size_t>(forList / rowHeight);
    }

    Action VoiceNotesScreen::draw(ui::Context& ui, VoiceNotesModel& model, uint32_t, Screen& screen) {
        const ui::Rect content = detail::content(ui);
        const size_t capacity = visibleRowCount(content.h, kRowHeight, kReservedHeight);

        // Keep the selection on screen, scrolling by whole rows.
        if (model.selected < firstVisible_) {
            firstVisible_ = model.selected;
        } else if (capacity > 0 && model.selected >= firstVisible_ + capacity) {
            firstVisible_ = model.selected - capacity + 1;
        }
        if (firstVisible_ > model.rows.size()) {
            firstVisible_ = 0;
        }

        ui::Column list{content, kGap, 0};

        if (model.rows.empty()) {
            // An empty screen with no words reads as a fault. Say it is empty.
            ui.label(list.next(28), "Nenhuma nota pendente", 2, ui::themes::ColorRole::Foreground,
                     ui::TextAlign::Center);
            ui.label(list.next(24), "Duplo clique no BOOT durante a leitura", 1, ui::themes::ColorRole::Muted,
                     ui::TextAlign::Center, 2);
        } else {
            const size_t last = std::min(model.rows.size(), firstVisible_ + capacity);
            for (size_t index = firstVisible_; index < last; ++index) {
                const auto& row = model.rows[index];
                const ui::Rect rect = list.next(kRowHeight);
                if (ui.setting(rect, row.label, row.detail, ui::SettingLayout::Inline)) {
                    model.selected = index;
                }
                if (index == model.selected) {
                    // A rule under the selected row: the play and send buttons act on it,
                    // so which one they mean has to be unambiguous.
                    ui.gfx().fillRect(rect.x, static_cast<int16_t>(rect.y + rect.h - 2), rect.w, 2,
                                      ui.color(ui::themes::ColorRole::Accent));
                    ui.markDrawn();
                }
            }
            if (model.rows.size() > capacity) {
                char position[40] = {};
                std::snprintf(position, sizeof(position), "%u de %u",
                              static_cast<unsigned>(model.selected + 1),
                              static_cast<unsigned>(model.rows.size()));
                ui.label(list.next(18), position, 1, ui::themes::ColorRole::Muted, ui::TextAlign::Right);
            }
        }

        // The controls are pinned to the bottom of the content area rather than flowing
        // after the list, so however long the list grows it cannot reach them.
        const int16_t controlsTop = static_cast<int16_t>(content.y + content.h - kReservedHeight);

        if (model.error != nullptr) {
            ui.label({content.x, controlsTop, content.w, 18}, model.error, 1, ui::themes::ColorRole::Accent,
                     ui::TextAlign::Center);
        }

        ui::Row buttons{{content.x, static_cast<int16_t>(controlsTop + 18 + kGap), content.w, kButtonHeight},
                        kGap, 0};
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
