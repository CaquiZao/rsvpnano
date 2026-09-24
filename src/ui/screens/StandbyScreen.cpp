#include "ui/screens/StandbyScreen.h"

#include <algorithm>
#include <bit>
#include <iterator>

#include "fonts/MatrixGlyphAtlas.h"
#include "screensavers/PackedGrid.h"
#include "ui/screens/Screens.h"

namespace screens {

    namespace {
        constexpr uint32_t kFrameMs = 160;
        constexpr uint32_t kVoronoiFrameMs = 120;
        constexpr uint32_t kMatrixFrameMs = 80;
        constexpr uint16_t kBlack = 0x0000;
        constexpr uint16_t kHead = 0xFFFF;

        // The rain sits on pure black rather than the theme background, so the
        // trail has to fade toward black -- ui.blend() fades toward the theme.
        constexpr uint16_t fadeToBlack(uint16_t color, uint8_t alpha) {
            const uint32_t red = ((color >> 11) & 0x1FU) * alpha / 255U;
            const uint32_t green = ((color >> 5) & 0x3FU) * alpha / 255U;
            const uint32_t blue = (color & 0x1FU) * alpha / 255U;
            return static_cast<uint16_t>((red << 11) | (green << 5) | blue);
        }

        // Trail opacity per level, from the faintest tail up to the full accent
        // just under the head. Level 0 is unlit and level 5 is the white head.
        constexpr uint8_t kTrailAlpha[] = {0, 46, 97, 166, 255};
        static_assert(std::size(kTrailAlpha) == standby::kGlyphHeadLevel);
        static_assert(standby::kMatrixCellWidth == fonts::kMatrixGlyphWidth);
        static_assert(standby::kMatrixCellHeight == fonts::kMatrixGlyphHeight);
    } // namespace

    void StandbyScreen::begin(ui::Context& ui, uint32_t nowMs, size_t bookIndex, size_t wordIndex, standby::Kind kind) {
        kind_ = kind;
        const bool glyphs = kind == standby::Kind::matrix;
        cellWidth_ = glyphs ? standby::kMatrixCellWidth : standby::kPackedCellSize;
        cellHeight_ = glyphs ? standby::kMatrixCellHeight : standby::kPackedCellSize;
        const standby::GridFit fit = standby::fitGrid(kind, static_cast<uint16_t>(std::max<int16_t>(1, ui.width())),
                                                      static_cast<uint16_t>(std::max<int16_t>(1, ui.height())));
        columns_ = fit.columns;
        rows_ = fit.rows;
        screensaver_.select(kind, columns_, rows_);
        const uint32_t seed =
            nowMs ^ (static_cast<uint32_t>(bookIndex) << 16U) ^ (static_cast<uint32_t>(wordIndex) * 2654435761UL);
        screensaver_.seed(seed == 0 ? 1U : seed);
        nextFrameMs_ = nowMs;
    }

    void StandbyScreen::reset() {
        screensaver_.reset();
    }

    void StandbyScreen::update(ui::Context& ui, uint32_t nowMs) {
        if (static_cast<int32_t>(nowMs - nextFrameMs_) < 0)
            return;
        const uint32_t frameMs = kind_ == standby::Kind::voronoi  ? kVoronoiFrameMs
                               : kind_ == standby::Kind::matrix   ? kMatrixFrameMs
                                                                  : kFrameMs;
        screensaver_.step();
        nextFrameMs_ = nowMs + frameMs;
        draw(ui);
    }

    void StandbyScreen::draw(ui::Context& ui) {
        if (!screensaver_)
            return;
        const standby::Frame frame = screensaver_.frame();
        ui.beginFrame(static_cast<uint8_t>(Screen::Standby));
        if (columns_ == 0 || rows_ == 0 || (frame.cells.empty() && frame.glyphCells.empty())) {
            ui.endFrame();
            return;
        }

        Arduino_GFX& gfx = ui.gfx();
        const int16_t originX = static_cast<int16_t>((ui.width() - columns_ * cellWidth_) / 2);
        const int16_t originY = static_cast<int16_t>((ui.height() - rows_ * cellHeight_) / 2);
        if (!frame.glyphCells.empty()) {
            drawGlyphs(ui, frame, originX, originY);
            ui.endFrame();
            return;
        }

        const uint16_t dim = ui.blend(ui::themes::ColorRole::Foreground, 72);
        const uint16_t bright = kind_ == standby::Kind::life ? ui.color(ui::themes::ColorRole::Foreground)
                                                             : ui.color(ui::themes::ColorRole::Accent);
        const size_t cellCount = static_cast<size_t>(columns_) * rows_;
        const auto drawRun = [&](size_t first, size_t last, uint16_t color) {
            const uint16_t x = static_cast<uint16_t>(first % columns_);
            const uint16_t y = static_cast<uint16_t>(first / columns_);
            gfx.fillRect(static_cast<int16_t>(originX + x * cellWidth_),
                         static_cast<int16_t>(originY + y * cellHeight_),
                         static_cast<int16_t>((last - first + 1U) * cellWidth_), cellHeight_, color);
        };
        if (frame.fullRedraw || frame.dirtyCells.empty()) {
            gfx.fillScreen(ui.color(ui::themes::ColorRole::Background));
            const auto drawCells = [&](standby::PackedGridView cells, uint16_t color) {
                size_t runStart = cellCount;
                size_t runEnd = 0;
                for (size_t wordIndex = 0; wordIndex < cells.size(); ++wordIndex) {
                    uint32_t bits = cells[wordIndex];
                    while (bits != 0) {
                        const size_t index = wordIndex * standby::kPackedBitsPerWord + std::countr_zero(bits);
                        if (index >= cellCount)
                            break;
                        if (runStart < cellCount && (index != runEnd + 1U || index % columns_ == 0)) {
                            drawRun(runStart, runEnd, color);
                            runStart = cellCount;
                        }
                        if (runStart == cellCount)
                            runStart = index;
                        runEnd = index;
                        bits &= bits - 1U;
                    }
                }
                if (runStart < cellCount)
                    drawRun(runStart, runEnd, color);
            };
            if (!frame.dimCells.empty())
                drawCells(frame.dimCells, dim);
            drawCells(frame.cells, bright);
            ui.markDrawn();
        } else {
            size_t runStart = cellCount;
            size_t runEnd = 0;
            uint16_t runColor = 0;
            for (size_t wordIndex = 0; wordIndex < frame.dirtyCells.size(); ++wordIndex) {
                uint32_t bits = frame.dirtyCells[wordIndex];
                while (bits != 0) {
                    const unsigned bit = std::countr_zero(bits);
                    const uint32_t mask = 1UL << bit;
                    const size_t index = wordIndex * standby::kPackedBitsPerWord + bit;
                    if (index >= cellCount)
                        break;
                    const uint16_t color = (frame.cells[wordIndex] & mask) != 0 ? bright
                                         : !frame.dimCells.empty() && (frame.dimCells[wordIndex] & mask) != 0
                                             ? dim
                                             : ui.color(ui::themes::ColorRole::Background);
                    if (runStart < cellCount && (index != runEnd + 1U || index % columns_ == 0 || color != runColor)) {
                        drawRun(runStart, runEnd, runColor);
                        runStart = cellCount;
                    }
                    if (runStart == cellCount) {
                        runStart = index;
                        runColor = color;
                    }
                    runEnd = index;
                    bits &= bits - 1U;
                }
            }
            if (runStart < cellCount) {
                drawRun(runStart, runEnd, runColor);
                ui.markDrawn();
            }
        }
        ui.endFrame();
    }

    void StandbyScreen::drawGlyphs(ui::Context& ui, const standby::Frame& frame, int16_t originX, int16_t originY) {
        Arduino_GFX& gfx = ui.gfx();
        const uint16_t accent = ui.color(ui::themes::ColorRole::Accent);
        uint16_t levelColor[standby::kGlyphLevelCount];
        for (uint8_t level = 0; level < standby::kGlyphHeadLevel; ++level)
            levelColor[level] = fadeToBlack(accent, kTrailAlpha[level]);
        levelColor[standby::kGlyphHeadLevel] = kHead;

        const size_t cellCount = frame.glyphCells.size();
        const auto drawCell = [&](size_t index) {
            const standby::GlyphCell cell = frame.glyphCells[index];
            const int16_t x = static_cast<int16_t>(originX + (index % columns_) * cellWidth_);
            const int16_t y = static_cast<int16_t>(originY + (index / columns_) * cellHeight_);
            if (cell.level == 0) {
                gfx.fillRect(x, y, cellWidth_, cellHeight_, kBlack);
                return;
            }
            // drawBitmap paints the background in the same pass, so the previous
            // glyph never needs a separate erase.
            gfx.drawBitmap(x, y,
                           &fonts::kMatrixGlyphBitmaps[static_cast<size_t>(cell.glyph) * fonts::kMatrixGlyphStride],
                           fonts::kMatrixGlyphWidth, fonts::kMatrixGlyphHeight, levelColor[cell.level], kBlack);
        };

        if (frame.fullRedraw || frame.dirtyCells.empty()) {
            gfx.fillScreen(kBlack);
            for (size_t index = 0; index < cellCount; ++index) {
                if (frame.glyphCells[index].level != 0)
                    drawCell(index);
            }
            ui.markDrawn();
            return;
        }

        bool drew = false;
        for (size_t wordIndex = 0; wordIndex < frame.dirtyCells.size(); ++wordIndex) {
            uint32_t bits = frame.dirtyCells[wordIndex];
            while (bits != 0) {
                const size_t index = wordIndex * standby::kPackedBitsPerWord + std::countr_zero(bits);
                if (index >= cellCount)
                    break;
                drawCell(index);
                drew = true;
                bits &= bits - 1U;
            }
        }
        if (drew)
            ui.markDrawn();
    }

} // namespace screens
