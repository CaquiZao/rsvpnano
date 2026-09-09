#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

#include "ui/Ui.h"
#include "ui/screens/Screens.h"

namespace screens {

    struct VoiceNoteRow {
        std::string label;  // "09/09 14:03", or "sem relogio" when SNTP never landed
        std::string detail; // duration, and whether the book anchor travelled with it
        std::string path;   // what the play button hands to the player
    };

    struct VoiceNotesModel {
        std::vector<VoiceNoteRow> rows;
        bool busy = false;      // an upload is in flight
        bool playing = false;   // a note is being played back
        size_t selected = 0;
        const char* error = nullptr;
    };

    // A recording is named after the moment it was taken, so the list can be labelled
    // without opening a single sidecar.
    std::string labelFromRecordingName(std::string_view name);

    // "17s", "1m20s". Bytes are meaningless to the reader; seconds are what tells one
    // note from another at a glance.
    std::string formatDuration(uint32_t durationMs);

    // How many rows fit above the pinned buttons. Returned rather than assumed so the
    // list can never push the controls off the bottom of a 172 px panel.
    size_t visibleRowCount(int16_t contentHeight, int16_t rowHeight, int16_t reservedHeight);

    class VoiceNotesScreen {
    public:
        Action draw(ui::Context& ui, VoiceNotesModel& model, uint32_t nowMs, Screen& screen);

    private:
        size_t firstVisible_ = 0;
    };

} // namespace screens
