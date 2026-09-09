#include "voice/DoubleClick.h"

namespace voice {

    DoubleClick::Verdict DoubleClick::onPress(uint32_t nowMs) {
        if (waiting_) {
            // Unsigned subtraction wraps correctly, so a press either side of the
            // millis() rollover is still measured as the short interval it was.
            const uint32_t sinceFirst = nowMs - firstPressMs_;
            if (sinceFirst <= kWindowMs) {
                waiting_ = false;
                return Verdict::Double;
            }
        }
        firstPressMs_ = nowMs;
        waiting_ = true;
        return Verdict::Nothing;
    }

    DoubleClick::Verdict DoubleClick::tick(uint32_t nowMs) {
        if (!waiting_) {
            return Verdict::Nothing;
        }
        if ((nowMs - firstPressMs_) <= kWindowMs) {
            return Verdict::Nothing;
        }
        waiting_ = false;
        return Verdict::Single;
    }

    void DoubleClick::reset() {
        waiting_ = false;
    }

} // namespace voice
