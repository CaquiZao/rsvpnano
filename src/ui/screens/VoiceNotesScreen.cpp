#include "ui/screens/VoiceNotesScreen.h"

#include <algorithm>
#include <cstdio>

#include "ui/screens/ScreenCommon.h"

namespace screens {

    namespace {

        // How many rows fit before the list stops being readable on a 172 px panel.
        constexpr size_t kMaxRows = 5;

        bool allDigits(std::string_view text) {
            return std::all_of(text.begin(), text.end(), [](char c) { return c >= '0' && c <= '9'; });
        }

    } // namespace

    std::string labelFromRecordingName(std::string_view name) {
        // Names are "YYYYMMDD-HHMMSS.wav" with a clock, "boot-NNNNNNNN.wav" without.
        const size_t dot = name.find_last_of('.');
        const std::string_view stem = dot == std::string_view::npos ? name : name.substr(0, dot);

        if (stem.size() >= 4 && stem.substr(0, 5) == "boot-") {
            return "sem relogio";
        }
        const size_t dash = stem.find('-');
        if (dash != 8 || stem.size() < 15 || !allDigits(stem.substr(0, 8))
            || !allDigits(stem.substr(9, 6))) {
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

    Action VoiceNotesScreen::draw(ui::Context& ui, const VoiceNotesModel& model, uint32_t, Screen& screen) {
        const ui::Rect content = detail::content(ui);
        ui::Column column{content, 4, 0};

        if (model.rows.empty()) {
            // An empty screen with no text reads as a fault. Say it is empty.
            ui.label(column.next(28), "Nenhuma nota pendente", 2, ui::themes::ColorRole::Foreground,
                     ui::TextAlign::Center);
            ui.label(column.next(24), "Duplo clique no BOOT durante a leitura", 1,
                     ui::themes::ColorRole::Muted, ui::TextAlign::Center, 2);
        } else {
            for (size_t index = 0; index < model.rows.size() && index < kMaxRows; ++index) {
                const auto& row = model.rows[index];
                ui.setting(column.next(26), row.label, row.detail, ui::SettingLayout::Inline);
            }
            if (model.rows.size() > kMaxRows) {
                char more[32] = {};
                std::snprintf(more, sizeof(more), "+%u mais",
                              static_cast<unsigned>(model.rows.size() - kMaxRows));
                ui.label(column.next(20), more, 1, ui::themes::ColorRole::Muted, ui::TextAlign::Right);
            }
        }

        if (model.error != nullptr) {
            ui.label(column.next(20), model.error, 1, ui::themes::ColorRole::Accent, ui::TextAlign::Center);
        }

        // Disabled while a flush is running: a second request would only queue behind
        // the first and the button would lie about having done something.
        if (ui.button(column.next(30), model.busy ? "Enviando..." : "Enviar agora", !model.busy,
                      ui::Icon::None, 2)
            && !model.busy) {
            return Action::VoiceFlush;
        }
        if (ui.button(column.next(30), "Voltar", true, ui::Icon::None, 2)) {
            screen = Screen::Device;
        }
        return Action::None;
    }

} // namespace screens
