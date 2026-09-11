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
        bool busy = false;    // an upload is in flight
        bool playing = false; // a note is being played back
        size_t selected = 0;
        const char* error = nullptr;
        // What the last finished flush delivered, e.g. "1 nota enviada". Empty most
        // of the time. Without it, a successful send looks identical to a broken
        // button: the queue empties, so the send button correctly goes dead, and
        // nothing on screen says anything happened.
        std::string notice;
    };

    // A recording is named after the moment it was taken, so the list can be labelled
    // without opening a single sidecar.
    std::string labelFromRecordingName(std::string_view name);

    // "17s", "1m20s". Bytes mean nothing to a reader; seconds are what tells one note
    // from another at a glance.
    std::string formatDuration(uint32_t durationMs);

    // "1 nota enviada", "3 notas enviadas", and nothing at all for zero: a flush that
    // delivered nothing has no good news, and the error line is what explains why.
    std::string sentNotice(size_t sent);

    // The strip the list scrolls in: whatever the header and the button row do not
    // need. The controls are laid out first on purpose. Letting the list take the
    // space instead is what pushed the back button off the bottom as recordings piled
    // up, and a screen you cannot leave is worse than one that shows fewer rows.
    ui::Rect listViewport(const ui::Rect& content);

    // Scrolls by drag, like the chapter list, because that is the gesture this device
    // already teaches. The rows live inside one redraw-guarded viewport: mixing slot
    // widgets with raw drawing in a scrolling area corrupts the slot cache and leaves
    // half-painted buttons behind.
    class VoiceNotesScreen {
    public:
        Action draw(ui::Context& ui, VoiceNotesModel& model, uint32_t nowMs, Screen& screen);

    private:
        size_t rowCount_ = 0;
        size_t dragStartIndex_ = 0;
        int16_t offset_ = 0;
        uint16_t lastY_ = 0;
        uint16_t dragDistance_ = 0;
        uint32_t lastTickMs_ = 0;
        int32_t velocity_ = 0;
        int32_t scrollRemainder_ = 0;
        bool dragging_ = false;
    };

} // namespace screens
