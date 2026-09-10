#pragma once

#include <cstddef>
#include <string>
#include <string_view>
#include <vector>

namespace voice {

    // The part of the mark file that has no filesystem in it, so it can be tested on
    // the PC. See VoiceMarks.h for what reads and writes it.

    // "/voice/marks/<slug>.txt". The slug is a book stem, already sanitised by
    // bookSlug(); anything with a separator in it would escape the directory.
    std::string marksPath(std::string_view bookSlug);

    // Tolerant of a truncated last line: the file is appended to on a device that can
    // lose power mid-write, and one unreadable line must not cost the rest.
    std::vector<size_t> parseMarks(std::string_view text);

} // namespace voice
