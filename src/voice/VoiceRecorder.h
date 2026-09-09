#pragma once

#include <cstdint>
#include <string>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "voice/VoiceNoteMeta.h"

namespace voice {

    // Owns one recording at a time. captureToFile() blocks for as long as the user
    // talks, so it runs on its own task and the UI polls elapsedMs() and level() to
    // draw the waveform.
    class Recorder {
    public:
        // The gain measured on this board: 24 dB lands around -21 dBFS for a normal
        // speaking voice, with headroom. See the spec for the measurement.
        static constexpr uint8_t kMicGain = 8;

        // `anchor` carries the book position; the recorder fills in the timing.
        bool start(const NoteMeta& anchor);
        // Asks the capture loop to finish. Never kills the task: that could abandon a
        // half-written block and leave a WAV the bridge has to repair.
        void requestStop();

        bool active() const;
        uint32_t elapsedMs() const;
        uint8_t level() const;
        const char* error() const;

        // True once the task has written the sidecar and gone. Clears on read, so the
        // caller learns about a finished recording exactly once.
        bool takeFinished();
        // Duration of the recording that just finished, for the confirmation line.
        uint32_t lastDurationMs() const;

    private:
        static void taskEntry(void* self);
        void run();

        NoteMeta anchor_;
        std::string path_;
        volatile bool active_ = false;
        volatile bool stopRequested_ = false;
        volatile bool finished_ = false;
        volatile uint32_t elapsedMs_ = 0;
        volatile uint32_t lastDurationMs_ = 0;
        volatile uint8_t level_ = 0;
        const char* error_ = nullptr;
    };

} // namespace voice
