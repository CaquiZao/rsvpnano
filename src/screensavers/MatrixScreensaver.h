#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "screensavers/ScreensaverTypes.h"

namespace standby {

    // Glyph rain: each column drops a bright head that leaves a fading trail of
    // characters behind it. Unlike the packed-bit savers this one reports a
    // glyph and a brightness step per cell, so the renderer draws bitmaps.
    class MatrixScreensaver final {
    public:
        void reset(uint16_t columns, uint16_t rows);
        void seed(uint32_t rngSeed);
        void step();
        Frame frame() const;

    private:
        struct Column {
            int16_t head = 0; // head row, in sixteenths of a cell
            int16_t speed = 0; // fall rate, in sixteenths of a cell per step
            uint8_t length = 0; // trail length, in cells
            uint8_t restDelay = 0; // steps to wait before falling again
        };

        void restartColumn(Column& column);
        void render();

        uint16_t columns_ = 0;
        uint16_t rows_ = 0;
        uint32_t rng_ = 1;
        uint32_t generation_ = 0;
        bool fullRedraw_ = true;
        size_t wordCount_ = 0;
        std::array<Column, kMaxMatrixColumns> columnState_{};
        std::array<GlyphCell, kMaxMatrixCells> cells_{};
        PackedGridStorage dirtyCells_{};
    };

} // namespace standby
