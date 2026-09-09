#pragma once

#include <cstdint>
#include <string>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace voice {

    // Plays a recorded WAV back through the speaker. A note you cannot hear is a note
    // you cannot trust: without playback the only way to check what the microphone
    // caught is to wait for the transcription.
    class Player {
    public:
        bool play(const std::string& wavPath);
        void requestStop();

        bool active() const;
        uint32_t elapsedMs() const;
        const char* error() const;

    private:
        static void taskEntry(void* self);
        void run();

        std::string path_;
        volatile bool active_ = false;
        volatile bool stopRequested_ = false;
        volatile uint32_t elapsedMs_ = 0;
        const char* error_ = nullptr;
    };

} // namespace voice
