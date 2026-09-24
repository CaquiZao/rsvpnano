#pragma once

#include <algorithm>
#include <cstdint>
#include <span>

#include "screensavers/PackedGrid.h"

namespace standby {

    enum class Kind : uint8_t {
        life = 0,
        maze = 1,
        voronoi = 2,
        screenOff = 3,
        reaction = 4,
        matrix = 5,
        Count,
    };

    // Glyph rain cells carry a character and a brightness step, which the packed
    // bit layers cannot express. Level 0 is unlit; kGlyphHeadLevel is the bright
    // head of a falling column and the levels below it are the fading trail.
    constexpr uint8_t kGlyphHeadLevel = 5;
    constexpr uint8_t kGlyphLevelCount = kGlyphHeadLevel + 1;

    constexpr uint16_t kMaxMatrixColumns = 80; // 640 px wide panel / 8 px cell
    constexpr uint16_t kMaxMatrixRows = 50; // 600 px tall panel / 12 px cell
    constexpr size_t kMaxMatrixCells = static_cast<size_t>(kMaxMatrixColumns) * kMaxMatrixRows;

    constexpr uint8_t kMatrixCellWidth = 8;
    constexpr uint8_t kMatrixCellHeight = 12;
    constexpr uint8_t kPackedCellSize = 4;

    struct GridFit {
        uint16_t columns;
        uint16_t rows;
    };

    // How many cells of a kind fit on a panel. Packed-bit savers round up so
    // their blocks cover the panel edge to edge; a glyph grid rounds down,
    // because a character sliced by the screen edge reads as a rendering bug.
    constexpr GridFit fitGrid(Kind kind, uint16_t width, uint16_t height) {
        const bool glyphs = kind == Kind::matrix;
        const uint16_t cellWidth = glyphs ? kMatrixCellWidth : kPackedCellSize;
        const uint16_t cellHeight = glyphs ? kMatrixCellHeight : kPackedCellSize;
        const uint16_t columns = glyphs ? static_cast<uint16_t>(width / cellWidth)
                                        : static_cast<uint16_t>((width + cellWidth - 1U) / cellWidth);
        const uint16_t rows = glyphs ? static_cast<uint16_t>(height / cellHeight)
                                     : static_cast<uint16_t>((height + cellHeight - 1U) / cellHeight);
        return {std::clamp<uint16_t>(columns, 1, glyphs ? kMaxMatrixColumns : kMaxStandbyColumns),
                std::clamp<uint16_t>(rows, 1, glyphs ? kMaxMatrixRows : kMaxStandbyRows)};
    }

    struct GlyphCell {
        uint8_t glyph = 0;
        uint8_t level = 0;
    };

    using GlyphCellView = std::span<const GlyphCell>;

    struct Frame {
        PackedGridView cells{}; // bright/live cells, packed bits
        PackedGridView dimCells{}; // optional dim layer, packed bits
        PackedGridView dirtyCells{}; // cells that changed; invalid means redraw all
        uint32_t generation = 0;
        bool fullRedraw = true;
        // Trails after fullRedraw so the packed-bit savers keep initialising
        // Frame positionally. Empty unless the saver draws glyphs.
        GlyphCellView glyphCells{};
    };

} // namespace standby
