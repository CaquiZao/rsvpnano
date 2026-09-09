#pragma once

#include <cstdint>

namespace voice {

    // Turns a stream of presses into single and double clicks.
    //
    // A single click is reported late, only once the window closes without a second
    // press. That delay is the whole cost of the feature: play/pause on the reader
    // becomes 260 ms slower in exchange for being able to start a recording without
    // leaving the page.
    class DoubleClick {
    public:
        enum class Verdict : uint8_t { Nothing, Single, Double };

        // 260 ms: comfortably above a deliberate double tap and comfortably below the
        // point where the pause feels broken.
        static constexpr uint32_t kWindowMs = 260;

        // Call on every press.
        Verdict onPress(uint32_t nowMs);
        // Call every frame so a pending single can expire into a verdict.
        Verdict tick(uint32_t nowMs);
        // Drop any pending press, e.g. when leaving the reader.
        void reset();

    private:
        uint32_t firstPressMs_ = 0;
        bool waiting_ = false;
    };

} // namespace voice
