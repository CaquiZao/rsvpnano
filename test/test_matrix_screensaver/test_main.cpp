#include <unity.h>

#include <optional>
#include <vector>

#include "fonts/MatrixGlyphAtlas.h"
#include "screensavers/MatrixScreensaver.h"
#include "screensavers/Screensaver.h"

namespace {

    constexpr uint16_t kColumns = 40;
    constexpr uint16_t kRows = 14;
    constexpr uint32_t kSeed = 12345;

    standby::MatrixScreensaver makeRain() {
        standby::MatrixScreensaver rain;
        rain.reset(kColumns, kRows);
        rain.seed(kSeed);
        return rain;
    }

    const standby::GlyphCell& cellAt(const standby::Frame& frame, uint16_t column, uint16_t row) {
        return frame.glyphCells[static_cast<size_t>(row) * kColumns + column];
    }

    // The single lit head of a column, if one is on screen right now.
    std::optional<uint16_t> headRow(const standby::Frame& frame, uint16_t column) {
        for (uint16_t row = 0; row < kRows; ++row) {
            if (cellAt(frame, column, row).level == standby::kGlyphHeadLevel)
                return row;
        }
        return std::nullopt;
    }

} // namespace

void setUp() {}
void tearDown() {}

void test_matrix_replays_identically_for_a_given_seed() {
    standby::MatrixScreensaver left = makeRain();
    standby::MatrixScreensaver right = makeRain();

    for (int generation = 0; generation < 60; ++generation) {
        const standby::Frame a = left.frame();
        const standby::Frame b = right.frame();
        TEST_ASSERT_EQUAL_UINT32(a.generation, b.generation);
        TEST_ASSERT_EQUAL(a.glyphCells.size(), b.glyphCells.size());
        TEST_ASSERT_GREATER_THAN_UINT32(0, a.glyphCells.size());
        for (size_t i = 0; i < a.glyphCells.size(); ++i) {
            TEST_ASSERT_EQUAL_UINT8(a.glyphCells[i].glyph, b.glyphCells[i].glyph);
            TEST_ASSERT_EQUAL_UINT8(a.glyphCells[i].level, b.glyphCells[i].level);
        }
        left.step();
        right.step();
    }
}

void test_matrix_seeds_a_full_redraw_then_incremental_frames() {
    standby::MatrixScreensaver rain = makeRain();

    TEST_ASSERT_TRUE(rain.frame().fullRedraw);
    TEST_ASSERT_EQUAL_UINT32(0, rain.frame().generation);

    rain.step();

    TEST_ASSERT_FALSE(rain.frame().fullRedraw);
    TEST_ASSERT_EQUAL_UINT32(1, rain.frame().generation);
}

void test_matrix_heads_only_ever_move_downward() {
    standby::MatrixScreensaver rain = makeRain();
    bool sawAnyColumnAdvance = false;

    for (int generation = 0; generation < 80; ++generation) {
        std::vector<std::optional<uint16_t>> before;
        for (uint16_t column = 0; column < kColumns; ++column)
            before.push_back(headRow(rain.frame(), column));

        rain.step();

        for (uint16_t column = 0; column < kColumns; ++column) {
            const std::optional<uint16_t> after = headRow(rain.frame(), column);
            if (!before[column] || !after)
                continue;
            // A slow column holds the same row for a few steps, so the
            // invariant is that a head never climbs -- not that it always moves.
            const bool held = *after == *before[column];
            const bool fell = *after > *before[column];
            const bool restarted = *after == 0;
            TEST_ASSERT_TRUE_MESSAGE(held || fell || restarted, "a head moved upward mid-fall");
            sawAnyColumnAdvance = sawAnyColumnAdvance || fell;
        }
    }

    TEST_ASSERT_TRUE_MESSAGE(sawAnyColumnAdvance, "no column ever advanced");
}

void test_matrix_trail_fades_upward_from_the_head() {
    standby::MatrixScreensaver rain = makeRain();

    for (int generation = 0; generation < 40; ++generation) {
        const standby::Frame frame = rain.frame();
        for (uint16_t column = 0; column < kColumns; ++column) {
            const std::optional<uint16_t> head = headRow(frame, column);
            if (!head)
                continue;
            uint8_t previous = standby::kGlyphHeadLevel;
            for (int row = static_cast<int>(*head) - 1; row >= 0; --row) {
                const uint8_t level = cellAt(frame, column, static_cast<uint16_t>(row)).level;
                TEST_ASSERT_LESS_OR_EQUAL_UINT8_MESSAGE(previous, level, "trail brightened going up");
                previous = level;
            }
            // Nothing is lit below the head; the column has not reached there yet.
            for (uint16_t row = static_cast<uint16_t>(*head + 1); row < kRows; ++row)
                TEST_ASSERT_EQUAL_UINT8_MESSAGE(0, cellAt(frame, column, row).level, "lit cell below the head");
        }
        rain.step();
    }
}

void test_matrix_dirty_bits_match_exactly_the_cells_that_changed() {
    standby::MatrixScreensaver rain = makeRain();

    for (int generation = 0; generation < 40; ++generation) {
        const standby::Frame previous = rain.frame();
        const std::vector<standby::GlyphCell> before{previous.glyphCells.begin(), previous.glyphCells.end()};

        rain.step();

        const standby::Frame frame = rain.frame();
        for (size_t i = 0; i < before.size(); ++i) {
            const bool changed =
                before[i].glyph != frame.glyphCells[i].glyph || before[i].level != frame.glyphCells[i].level;
            TEST_ASSERT_EQUAL_MESSAGE(changed, standby::cellAlive(frame.dirtyCells, i),
                                      "dirty bit disagrees with the cell that changed");
        }
    }
}

void test_matrix_keeps_levels_and_glyph_indices_in_range() {
    standby::MatrixScreensaver rain = makeRain();

    for (int generation = 0; generation < 40; ++generation) {
        for (const standby::GlyphCell& cell: rain.frame().glyphCells) {
            TEST_ASSERT_LESS_THAN_UINT8(standby::kGlyphLevelCount, cell.level);
            TEST_ASSERT_LESS_THAN_UINT8(fonts::kMatrixGlyphCount, cell.glyph);
        }
        rain.step();
    }
}

void test_matrix_survives_a_degenerate_grid() {
    standby::MatrixScreensaver rain;
    rain.reset(0, 0);
    rain.seed(kSeed);
    rain.step();

    TEST_ASSERT_TRUE(rain.frame().glyphCells.empty());
}

void test_screensaver_slot_exposes_glyph_cells_for_matrix() {
    standby::ScreensaverSlot slot;
    slot.select(standby::Kind::matrix, kColumns, kRows);
    TEST_ASSERT_TRUE(static_cast<bool>(slot));

    slot.seed(kSeed);
    slot.step();

    const standby::Frame frame = slot.frame();
    TEST_ASSERT_EQUAL(static_cast<size_t>(kColumns) * kRows, frame.glyphCells.size());
    TEST_ASSERT_TRUE_MESSAGE(frame.cells.empty(), "the glyph saver must not claim the packed-bit layer");

    slot.reset();
    TEST_ASSERT_FALSE(static_cast<bool>(slot));
}

void test_glyph_grid_never_overflows_the_panel() {
    const standby::GridFit fit = standby::fitGrid(standby::Kind::matrix, 640, 172);

    TEST_ASSERT_EQUAL_UINT16(80, fit.columns);
    TEST_ASSERT_EQUAL_UINT16(14, fit.rows);
    TEST_ASSERT_LESS_OR_EQUAL_UINT16(640, fit.columns * standby::kMatrixCellWidth);
    TEST_ASSERT_LESS_OR_EQUAL_UINT16(172, fit.rows * standby::kMatrixCellHeight);
}

void test_packed_grid_still_covers_the_panel_edge_to_edge() {
    const standby::GridFit fit = standby::fitGrid(standby::Kind::voronoi, 640, 172);

    TEST_ASSERT_EQUAL_UINT16(160, fit.columns);
    TEST_ASSERT_EQUAL_UINT16(43, fit.rows);
}

void test_grid_fit_clamps_to_the_buffer_limits() {
    const standby::GridFit wide = standby::fitGrid(standby::Kind::matrix, 4096, 4096);
    TEST_ASSERT_EQUAL_UINT16(standby::kMaxMatrixColumns, wide.columns);
    TEST_ASSERT_EQUAL_UINT16(standby::kMaxMatrixRows, wide.rows);

    const standby::GridFit tiny = standby::fitGrid(standby::Kind::matrix, 1, 1);
    TEST_ASSERT_EQUAL_UINT16(1, tiny.columns);
    TEST_ASSERT_EQUAL_UINT16(1, tiny.rows);
}

int main() {
    UNITY_BEGIN();
    RUN_TEST(test_matrix_replays_identically_for_a_given_seed);
    RUN_TEST(test_matrix_seeds_a_full_redraw_then_incremental_frames);
    RUN_TEST(test_matrix_heads_only_ever_move_downward);
    RUN_TEST(test_matrix_trail_fades_upward_from_the_head);
    RUN_TEST(test_matrix_dirty_bits_match_exactly_the_cells_that_changed);
    RUN_TEST(test_matrix_keeps_levels_and_glyph_indices_in_range);
    RUN_TEST(test_matrix_survives_a_degenerate_grid);
    RUN_TEST(test_screensaver_slot_exposes_glyph_cells_for_matrix);
    RUN_TEST(test_glyph_grid_never_overflows_the_panel);
    RUN_TEST(test_packed_grid_still_covers_the_panel_edge_to_edge);
    RUN_TEST(test_grid_fit_clamps_to_the_buffer_limits);
    return UNITY_END();
}
