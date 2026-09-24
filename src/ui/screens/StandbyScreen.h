#pragma once

#include "screensavers/Screensaver.h"
#include "ui/Ui.h"

namespace screens {

    class StandbyScreen {
    public:
        void begin(ui::Context& ui, uint32_t nowMs, size_t bookIndex, size_t wordIndex, standby::Kind kind);
        void reset();
        void update(ui::Context& ui, uint32_t nowMs);
        void draw(ui::Context& ui);

    private:
        void drawGlyphs(ui::Context& ui, const standby::Frame& frame, int16_t originX, int16_t originY);

        standby::ScreensaverSlot screensaver_;
        uint32_t nextFrameMs_ = 0;
        uint16_t columns_ = 0;
        uint16_t rows_ = 0;
        uint8_t cellWidth_ = 0;
        uint8_t cellHeight_ = 0;
        standby::Kind kind_ = standby::Kind::life;
    };

} // namespace screens
