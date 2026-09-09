#pragma once

#include <cstdint>

namespace voice {

    // Four sounds that can be told apart without looking at the screen. This is the
    // only feedback there is when the board is in a pocket, so they differ in contour
    // and not just pitch: a listener does not reliably name an absolute frequency.
    enum class Tone : uint8_t {
        Start, // rising pair
        Stop,  // falling pair
        Sent,  // two short high notes
        Error, // low note, twice
    };

    void play(Tone tone);

} // namespace voice
