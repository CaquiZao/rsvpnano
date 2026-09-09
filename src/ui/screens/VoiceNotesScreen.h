#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

#include "ui/Ui.h"
#include "ui/screens/Screens.h"

namespace screens {

    struct VoiceNoteRow {
        std::string label;  // "09/09 14:03" or "sem relogio"
        std::string detail; // size, and whether the anchor is there
    };

    struct VoiceNotesModel {
        std::vector<VoiceNoteRow> rows;
        bool busy = false;
        const char* error = nullptr;
    };

    // A recording is named after the moment it was taken, so the list can be labelled
    // without opening a single sidecar.
    std::string labelFromRecordingName(std::string_view name);

    class VoiceNotesScreen {
    public:
        Action draw(ui::Context& ui, const VoiceNotesModel& model, uint32_t nowMs, Screen& screen);
    };

} // namespace screens
