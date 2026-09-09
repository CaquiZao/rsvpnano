#include "voice/Tones.h"

#include "board/BoardAudio.h"

namespace voice {

    namespace {

        constexpr uint32_t kShortMs = 70;
        constexpr uint32_t kLongMs = 140;

        // A4, C#6 and A5: wide enough apart to be obvious over a small speaker.
        constexpr uint32_t kLow = 440;
        constexpr uint32_t kMid = 880;
        constexpr uint32_t kHigh = 1320;

    } // namespace

    void play(Tone tone) {
        switch (tone) {
            case Tone::Start:
                Board::Audio::playTone(kMid, kShortMs);
                Board::Audio::playTone(kHigh, kShortMs);
                return;
            case Tone::Stop:
                Board::Audio::playTone(kHigh, kShortMs);
                Board::Audio::playTone(kMid, kShortMs);
                return;
            case Tone::Sent:
                Board::Audio::playTone(kHigh, kShortMs);
                Board::Audio::playTone(kHigh, kShortMs);
                return;
            case Tone::Error:
                Board::Audio::playTone(kLow, kLongMs);
                Board::Audio::playTone(kLow, kLongMs);
                return;
        }
    }

} // namespace voice
