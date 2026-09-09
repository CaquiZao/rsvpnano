#include "voice/VoiceNoteMeta.h"

#include <cstdio>

namespace voice {

    namespace {

        void appendString(std::string& out, std::string_view key, std::string_view value) {
            out += '"';
            out += key;
            out += "\":\"";
            out += escapeJsonString(value);
            out += "\",";
        }

        void appendNumber(std::string& out, std::string_view key, uint32_t value) {
            out += '"';
            out += key;
            out += "\":";
            out += std::to_string(value);
            out += ',';
        }

    } // namespace

    std::string escapeJsonString(std::string_view text) {
        std::string out;
        out.reserve(text.size() + 8);
        for (const char raw : text) {
            const auto byte = static_cast<unsigned char>(raw);
            switch (raw) {
                case '"':
                    out += "\\\"";
                    continue;
                case '\\':
                    out += "\\\\";
                    continue;
                case '\n':
                    out += "\\n";
                    continue;
                case '\r':
                    out += "\\r";
                    continue;
                case '\t':
                    out += "\\t";
                    continue;
                default:
                    break;
            }
            // Only true control characters need the \u form. Bytes at 0x80 and above are
            // UTF-8 continuation and lead bytes; passing them through keeps the
            // Portuguese excerpt readable in the sidecar, and JSON is UTF-8 anyway.
            if (byte < 0x20) {
                char escape[7] = {};
                std::snprintf(escape, sizeof(escape), "\\u%04x", byte);
                out += escape;
                continue;
            }
            out += raw;
        }
        return out;
    }

    std::string toJson(const NoteMeta& meta) {
        std::string out = "{";
        if (!meta.recordedAt.empty()) {
            appendString(out, "recorded_at", meta.recordedAt);
        }
        out += "\"clock_synced\":";
        out += meta.clockSynced ? "true" : "false";
        out += ',';
        appendNumber(out, "duration_ms", meta.durationMs);
        if (!meta.book.empty()) {
            appendString(out, "book", meta.book);
            // Offset zero is a real position, so it travels with the book rather than
            // being suppressed as a falsy value.
            appendNumber(out, "word_offset", meta.wordOffset);
            if (!meta.excerpt.empty()) {
                appendString(out, "excerpt", meta.excerpt);
            }
        }
        if (out.back() == ',') {
            out.pop_back();
        }
        out += '}';
        return out;
    }

    std::string sidecarPath(std::string_view wavPath) {
        const size_t slash = wavPath.find_last_of('/');
        const size_t dot = wavPath.find_last_of('.');
        // A dot that sits in a directory name is not this file's extension.
        const bool hasExtension = dot != std::string_view::npos && (slash == std::string_view::npos || dot > slash);
        std::string out(hasExtension ? wavPath.substr(0, dot) : wavPath);
        out += ".json";
        return out;
    }

} // namespace voice
