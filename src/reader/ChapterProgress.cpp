#include "reader/ChapterProgress.h"

#include <algorithm>

namespace reading {

    namespace {

        uint8_t percentOf(size_t position, size_t span) {
            if (span == 0) {
                return 0;
            }
            const size_t clamped = std::min(position, span);
            return static_cast<uint8_t>((clamped * 100U) / span);
        }

    } // namespace

    ChapterPosition chapterPositionAt(std::span<const ChapterMarker> chapters, size_t wordIndex,
                                      size_t wordCount) {
        ChapterPosition position;
        position.count = chapters.size();
        position.percentInBook = percentOf(wordIndex, wordCount);

        if (chapters.empty()) {
            return position;
        }

        // The last marker at or before the position. A position before the first marker
        // is front matter, which belongs to chapter one rather than to nothing.
        size_t index = 0;
        for (size_t candidate = 0; candidate < chapters.size(); ++candidate) {
            if (chapters[candidate].wordIndex <= wordIndex) {
                index = candidate;
            } else {
                break;
            }
        }

        const size_t start = chapters[index].wordIndex;
        // No marker follows the last chapter, so its end is the end of the book.
        const size_t end = (index + 1 < chapters.size()) ? chapters[index + 1].wordIndex : wordCount;

        position.index = index;
        position.title = chapters[index].title;
        if (end > start && wordIndex > start) {
            position.percentInChapter = percentOf(wordIndex - start, end - start);
        }
        return position;
    }

} // namespace reading
