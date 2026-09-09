#include "voice/BookAnchor.h"

#include <cctype>

namespace voice {

    namespace {

        bool isSpace(char c) {
            return std::isspace(static_cast<unsigned char>(c)) != 0;
        }

        bool isUtf8Continuation(char c) {
            return (static_cast<unsigned char>(c) & 0xC0) == 0x80;
        }

        // How many bytes the codepoint starting with `lead` occupies.
        size_t sequenceLength(unsigned char lead) {
            if ((lead & 0x80) == 0x00) {
                return 1;
            }
            if ((lead & 0xE0) == 0xC0) {
                return 2;
            }
            if ((lead & 0xF0) == 0xE0) {
                return 3;
            }
            if ((lead & 0xF8) == 0xF0) {
                return 4;
            }
            return 1; // Not a lead byte at all; treat it as standalone.
        }

        // Collapses every run of whitespace to a single space and trims the ends. The
        // reader stores words, so a rebuilt paragraph carries whatever separators the
        // conversion left behind, and those would eat the excerpt budget.
        std::string normalizeWhitespace(std::string_view text) {
            std::string out;
            out.reserve(text.size());
            bool pendingSpace = false;
            for (const char c : text) {
                if (isSpace(c)) {
                    pendingSpace = !out.empty();
                    continue;
                }
                if (pendingSpace) {
                    out += ' ';
                    pendingSpace = false;
                }
                out += c;
            }
            return out;
        }

    } // namespace

    std::string bookSlug(std::string_view sourcePath) {
        if (sourcePath.empty()) {
            return {};
        }
        const size_t slash = sourcePath.find_last_of('/');
        std::string_view name = slash == std::string_view::npos ? sourcePath : sourcePath.substr(slash + 1);
        // Only the last dot is the extension: book names in this vault contain dots.
        const size_t dot = name.find_last_of('.');
        if (dot != std::string_view::npos && dot > 0) {
            name = name.substr(0, dot);
        }
        return std::string(name);
    }

    std::string clampExcerpt(std::string_view paragraph, size_t maxChars) {
        if (maxChars == 0) {
            return {};
        }
        std::string text = normalizeWhitespace(paragraph);
        if (text.size() <= maxChars) {
            return text;
        }

        text.resize(maxChars);

        // Prefer to end on the last complete word.
        const size_t lastSpace = text.find_last_of(' ');
        if (lastSpace != std::string::npos && lastSpace > 0) {
            text.resize(lastSpace);
            return text;
        }

        // A single word longer than the limit: cut it, but drop a trailing partial
        // UTF-8 sequence whole. Stripping only the continuation bytes would leave an
        // orphan lead byte, which is just as invalid and harder to notice.
        size_t start = text.size();
        while (start > 0 && isUtf8Continuation(text[start - 1])) {
            --start;
        }
        if (start > 0) {
            const auto lead = static_cast<unsigned char>(text[start - 1]);
            const size_t needed = sequenceLength(lead);
            if (needed > 1 && (text.size() - (start - 1)) < needed) {
                text.resize(start - 1);
            }
        }
        return text;
    }

} // namespace voice
