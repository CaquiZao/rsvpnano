#include "voice/Tones.h"

#include "board/BoardAudio.h"

namespace voice {

    namespace {

        constexpr uint32_t kShortMs = 45;
        constexpr uint32_t kLongMs = 90;

        // 1/15th of the amplitude the upstream beep uses. These cues fire while the
        // user is reading in a classroom: audible at arm's length, ignorable further
        // away. Loud feedback would make the feature unusable where it is most
        // wanted.
        constexpr int16_t kQuiet = 6000;
        // Register 0x32 in 0.5 dB steps, where 0xFF is roughly +32 dB: 0x60 lands
        // about 48 dB below what the codec boots with. Two attempts to quieten these
        // cues by scaling the samples failed because that digital gain swamped them,
        // so the volume is set where the gain actually is, and set low. Too quiet is
        // the safer error: this fires while the user is reading in a classroom.
        constexpr uint8_t kQuietDac = 0x70;

        // A4, C#6 and A5: wide enough apart to be obvious over a small speaker.
        constexpr uint32_t kLow = 440;
        constexpr uint32_t kMid = 880;
        constexpr uint32_t kHigh = 1320;

    } // namespace

    void play(Tone tone) {
        switch (tone) {
            case Tone::Start:
                Board::Audio::playTone(kMid, kShortMs, kQuiet, kQuietDac);
                Board::Audio::playTone(kHigh, kShortMs, kQuiet, kQuietDac);
                return;
            case Tone::Stop:
                Board::Audio::playTone(kHigh, kShortMs, kQuiet, kQuietDac);
                Board::Audio::playTone(kMid, kShortMs, kQuiet, kQuietDac);
                return;
            case Tone::Sent:
                Board::Audio::playTone(kHigh, kShortMs, kQuiet, kQuietDac);
                Board::Audio::playTone(kHigh, kShortMs, kQuiet, kQuietDac);
                return;
            case Tone::Error:
                Board::Audio::playTone(kLow, kLongMs, kQuiet, kQuietDac);
                Board::Audio::playTone(kLow, kLongMs, kQuiet, kQuietDac);
                return;
        }
    }

} // namespace voice
