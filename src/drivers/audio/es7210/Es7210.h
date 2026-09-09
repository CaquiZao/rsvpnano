#pragma once

#include <Arduino.h>
#include <Wire.h>

namespace BoardDrivers::Es7210 {

    // Gain codes are the ES7210's own 4-bit scale: 0 = 0 dB, then 3 dB per step up to
    // step 11 (33 dB). Named here because a bare number in a call site says nothing.
    constexpr uint8_t kGain24Db = 8;
    constexpr uint8_t kGain33Db = 11;

    // The ES7210 has four analog inputs wired as two pairs. Only Mic12 reaches the two
    // slots of plain stereo I2S; selecting Mic34 without also switching the codec and
    // the I2S peripheral to TDM yields exact silence, measured on hardware. The value
    // exists so the choice is explicit, not so both options work today.
    enum class MicPair : uint8_t { Mic12, Mic34 };

    struct Context {
        TwoWire& wire;
        // 7-bit. AD1/AD0 strapping picks one of 0x40..0x43; begin() finds which.
        uint8_t address = 0x40;
        bool available = false;
    };

    // Probes 0x40..0x43 and adopts whichever answers. The datasheet default is 0x40,
    // but Waveshare does not document this board's strapping, so guessing is not safe.
    bool begin(Context& context);

    // Full ADC bring-up: I2S slave, 16 kHz from a 256*fs MCLK, MIC1+MIC2 at `gain`.
    // The ESP32 is the I2S master here, so the codec only follows the clocks.
    bool prepareInput(Context& context, uint8_t gain = kGain24Db, MicPair pair = MicPair::Mic12);

    void dumpRegisters(Context& context);
    bool available(const Context& context);

    // Logs every address that acknowledges. Diagnostic only: one flash cycle then
    // answers "which chips are actually on this bus" without a logic analyser.
    void scanBus(TwoWire& wire);

} // namespace BoardDrivers::Es7210
