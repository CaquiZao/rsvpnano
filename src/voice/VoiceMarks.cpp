#include "voice/VoiceMarks.h"

#include <esp_log.h>

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        constexpr char kDir[] = "/voice/marks";
        // A book read end to end with a note every few pages stays well under this.
        // The cap exists so a runaway loop cannot fill the card with one file.
        constexpr size_t kMaxMarks = 512;

    } // namespace

    bool appendMark(fs::FS& fs, std::string_view bookSlug, size_t wordOffset) {
        if (!fs.exists(kDir) && !fs.mkdir(kDir)) {
            ESP_LOGW(kTag, "could not create %s", kDir);
            return false;
        }
        const std::string path = marksPath(bookSlug);
        File file = fs.open(path.c_str(), FILE_APPEND);
        if (!file) {
            ESP_LOGW(kTag, "could not open %s", path.c_str());
            return false;
        }
        const std::string line = std::to_string(wordOffset) + "\n";
        const size_t written = file.write(reinterpret_cast<const uint8_t*>(line.data()), line.size());
        file.close();
        return written == line.size();
    }

    std::vector<size_t> loadMarks(fs::FS& fs, std::string_view bookSlug) {
        File file = fs.open(marksPath(bookSlug).c_str());
        if (!file) {
            return {};
        }
        std::string text;
        text.resize(file.size());
        if (!text.empty()) {
            file.read(reinterpret_cast<uint8_t*>(text.data()), text.size());
        }
        file.close();

        std::vector<size_t> marks = parseMarks(text);
        if (marks.size() > kMaxMarks) {
            // Keep the most recent: those are the ones near where reading is now.
            marks.erase(marks.begin(), marks.end() - kMaxMarks);
        }
        return marks;
    }

} // namespace voice
