#include "voice/VoiceMarksPlan.h"

namespace voice {

    namespace {

        constexpr char kDir[] = "/voice/marks";

    } // namespace

    std::string marksPath(std::string_view bookSlug) {
        std::string name{bookSlug};
        if (name.empty()) {
            // Recordings made outside the reader still need somewhere to go, and an
            // empty filename is rejected by some drivers.
            name = "_";
        }
        return std::string{kDir} + "/" + name + ".txt";
    }

    std::vector<size_t> parseMarks(std::string_view text) {
        std::vector<size_t> marks;
        size_t value = 0;
        bool inNumber = false;
        for (const char c : text) {
            if (c >= '0' && c <= '9') {
                value = value * 10 + static_cast<size_t>(c - '0');
                inNumber = true;
                continue;
            }
            // Anything that is not a digit ends the number. A blank or junk line adds
            // nothing rather than a mark at word zero.
            if (inNumber) {
                marks.push_back(value);
            }
            value = 0;
            inNumber = false;
        }
        // A file cut short mid-append still ends in digits; those are a real position.
        if (inNumber) {
            marks.push_back(value);
        }
        return marks;
    }

} // namespace voice
