#pragma once

#include <cstddef>
#include <string_view>
#include <vector>

#include <FS.h>

#include "voice/VoiceMarksPlan.h"

namespace voice {

    // Where in a book the reader stopped to say something. Kept apart from the queue
    // on purpose: a note is deleted from the card once the bridge has it, and the mark
    // has to outlive that — otherwise the reader's own annotations vanish from the
    // progress bar minutes after being made.
    //
    // One file per book, one decimal word offset per line. Small enough that the whole
    // file is read when a book opens and never touched again while reading.

    // Appends one mark. Creates the directory on first use.
    bool appendMark(fs::FS& fs, std::string_view bookSlug, size_t wordOffset);

    // Empty when the book has no marks, which is the common case.
    std::vector<size_t> loadMarks(fs::FS& fs, std::string_view bookSlug);

} // namespace voice
