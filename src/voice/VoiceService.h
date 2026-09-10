#pragma once

#include <cstdint>
#include <functional>
#include <string>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

namespace voice {

    // Drains the queue in the background: brings the radio up, finds the bridge, sends
    // what is waiting, brings the radio down again. Runs on its own task because every
    // step of that blocks for seconds and the reader has to keep drawing.
    class Service {
    public:
        struct Credentials {
            std::string ssid;
            std::string password;
        };

        bool begin();

        // Called after a recording lands, and by the periodic tick. Cheap and safe from
        // any task: it only raises a semaphore.
        void requestFlush();

        // The radio is the biggest consumer on this board, so it is only woken when
        // there is something to send. Credentials are copied in, not held by reference:
        // the settings store is not this task's to read.
        void setCredentials(const Credentials& credentials);

        // Answers "is someone else using the radio right now". The update check
        // brings Wi-Fi up at boot and takes it down when it finishes, which killed an
        // upload mid-body. Waiting for it costs seconds; racing it costs the note.
        void setRadioGate(std::function<bool()> gate);

        // For the indicator in the reader. Cached, because counting means listing a
        // directory and the UI asks every frame.
        size_t pendingCount() const;
        bool busy() const;
        // Empty until an upload fails; cleared on the next success.
        const char* lastError() const;

    private:
        static void taskEntry(void* self);
        void run();
        bool waitForRadio();
        void flushOnce();

        SemaphoreHandle_t wake_ = nullptr;
        SemaphoreHandle_t lock_ = nullptr;
        Credentials credentials_;
        std::function<bool()> radioGate_;
        volatile size_t pendingCount_ = 0;
        volatile bool busy_ = false;
        const char* lastError_ = nullptr;
    };

} // namespace voice
