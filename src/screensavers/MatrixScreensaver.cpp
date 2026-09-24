#include "screensavers/MatrixScreensaver.h"

#include <algorithm>

#include "fonts/MatrixGlyphAtlas.h"

namespace standby {

    namespace {
        constexpr int32_t kFixedOne = 16; // one cell, in sixteenths
        constexpr int32_t kMinSpeed = 6; // 0.375 cells per step
        constexpr int32_t kMaxSpeed = 20; // 1.25 cells per step
        constexpr uint32_t kMinTrail = 5;
        constexpr uint32_t kMaxTrail = 14;
        constexpr uint32_t kMaxRestSteps = 40;
        constexpr uint32_t kStaggerRows = 8; // extra headroom above the top edge
        constexpr uint32_t kMutationOdds = 32; // 1 in N lit cells swaps glyph per step

        // Trail levels run from kGlyphHeadLevel - 1 down to 1, so the ramp spans
        // one less step than the head level itself.
        constexpr int32_t kTrailSpan = kGlyphHeadLevel - 2;
    } // namespace

    void MatrixScreensaver::reset(uint16_t columns, uint16_t rows) {
        columns_ = std::min<uint16_t>(columns, kMaxMatrixColumns);
        rows_ = std::min<uint16_t>(rows, kMaxMatrixRows);
        wordCount_ = packedWordCount(static_cast<size_t>(columns_) * rows_);
    }

    void MatrixScreensaver::seed(uint32_t rngSeed) {
        rng_ = rngSeed == 0 ? 1U : rngSeed;
        generation_ = 0;
        fullRedraw_ = true;
        const size_t cellCount = static_cast<size_t>(columns_) * rows_;
        std::fill_n(cells_.begin(), cellCount, GlyphCell{});
        for (uint16_t column = 0; column < columns_; ++column)
            restartColumn(columnState_[column]);
        render();
    }

    void MatrixScreensaver::step() {
        fullRedraw_ = false;
        ++generation_;
        for (uint16_t index = 0; index < columns_; ++index) {
            Column& column = columnState_[index];
            if (column.restDelay > 0) {
                if (--column.restDelay == 0)
                    restartColumn(column);
                continue;
            }
            column.head = static_cast<int16_t>(column.head + column.speed);
            // The head keeps falling until the whole trail has cleared the
            // bottom edge, then the column rests before starting over.
            const int32_t exhausted = (static_cast<int32_t>(rows_) + column.length) * kFixedOne;
            if (column.head >= exhausted)
                column.restDelay = static_cast<uint8_t>(1 + advanceRng(rng_) % kMaxRestSteps);
        }
        render();
    }

    Frame MatrixScreensaver::frame() const {
        const size_t cellCount = static_cast<size_t>(columns_) * rows_;
        return Frame{{},
                     {},
                     viewOf(dirtyCells_, wordCount_),
                     generation_,
                     fullRedraw_,
                     GlyphCellView{cells_.data(), cellCount}};
    }

    void MatrixScreensaver::restartColumn(Column& column) {
        // Staggering the start above the top edge is what keeps the columns from
        // marching in lockstep; no explicit density knob is needed.
        const uint32_t stagger = advanceRng(rng_) % (rows_ + kStaggerRows);
        column.head = static_cast<int16_t>(-static_cast<int32_t>(stagger) * kFixedOne);
        column.speed = static_cast<int16_t>(kMinSpeed + advanceRng(rng_) % (kMaxSpeed - kMinSpeed + 1));
        column.length = static_cast<uint8_t>(kMinTrail + advanceRng(rng_) % (kMaxTrail - kMinTrail + 1));
        column.restDelay = 0;
    }

    void MatrixScreensaver::render() {
        clearPackedGrid(dirtyCells_, wordCount_);
        const size_t cellCount = static_cast<size_t>(columns_) * rows_;
        if (cellCount == 0)
            return;

        for (uint16_t index = 0; index < columns_; ++index) {
            const Column& column = columnState_[index];
            const int32_t headRow = column.head >> 4; // arithmetic shift floors negatives
            const int32_t trail = column.length;
            const int32_t divisor = trail > 1 ? trail - 1 : 1;

            for (uint16_t row = 0; row < rows_; ++row) {
                const int32_t distance = headRow - static_cast<int32_t>(row);
                uint8_t level = 0;
                if (distance == 0)
                    level = kGlyphHeadLevel;
                else if (distance > 0 && distance <= trail)
                    level = static_cast<uint8_t>(kGlyphHeadLevel - 1 - (distance - 1) * kTrailSpan / divisor);

                const size_t cellIndex = static_cast<size_t>(row) * columns_ + index;
                GlyphCell& cell = cells_[cellIndex];
                const GlyphCell previous = cell;

                if (level == 0) {
                    cell = GlyphCell{};
                } else {
                    // A cell picks a character when the head lands on it, when a
                    // fast column skips straight past it into the trail, or when
                    // it randomly flickers to another glyph.
                    const bool arrived = level == kGlyphHeadLevel && previous.level != kGlyphHeadLevel;
                    const bool appeared = previous.level == 0;
                    const bool mutated = !arrived && !appeared && advanceRng(rng_) % kMutationOdds == 0;
                    if (arrived || appeared || mutated)
                        cell.glyph = static_cast<uint8_t>(advanceRng(rng_) % fonts::kMatrixGlyphCount);
                    cell.level = level;
                }

                if (cell.glyph != previous.glyph || cell.level != previous.level)
                    setCell(dirtyCells_, cellIndex, true);
            }
        }
    }

} // namespace standby
