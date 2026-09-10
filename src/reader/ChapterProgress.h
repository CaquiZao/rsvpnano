#pragma once

#include <cstddef>
#include <cstdint>
#include <span>
#include <string_view>

#include "library/BookMetadata.h"

namespace reading {

    struct ChapterPosition {
        size_t index = 0; // 0-based; meaningless when count == 0
        size_t count = 0;
        uint8_t percentInChapter = 0;
        uint8_t percentInBook = 0;
        std::string_view title;
        // The words this chapter spans, so a note taken inside it can be placed on
        // the chapter bar rather than only counted.
        size_t firstWord = 0;
        size_t lastWord = 0;
    };

    // Reading a whole chapter of a long book moves the book bar by under 4%, which
    // reads as no progress at all. The chapter percentage is the number that actually
    // moves while you read, so it is worth computing separately.
    //
    // `chapters` must be sorted by wordIndex, which is how BookMetadata stores them.
    ChapterPosition chapterPositionAt(std::span<const ChapterMarker> chapters, size_t wordIndex,
                                      size_t wordCount);

    // Where a note taken at `wordIndex` falls along a bar `width` pixels wide that
    // spans [firstWord, lastWord). Clamped to the bar: a note from another chapter
    // must not draw over whatever sits beside it.
    int16_t markOffset(int16_t width, size_t wordIndex, size_t firstWord, size_t lastWord);

} // namespace reading
