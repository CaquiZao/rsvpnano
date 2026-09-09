#include "drivers/audio/es7210/Es7210.h"

#include <esp_log.h>

namespace BoardDrivers::Es7210 {
    namespace {

        constexpr char kTag[] = "es7210";

        constexpr uint8_t kResetReg00 = 0x00;
        constexpr uint8_t kClockOffReg01 = 0x01;
        constexpr uint8_t kMainClkReg02 = 0x02;
        constexpr uint8_t kLrckDivHReg04 = 0x04;
        constexpr uint8_t kLrckDivLReg05 = 0x05;
        constexpr uint8_t kPowerDownReg06 = 0x06;
        constexpr uint8_t kOsrReg07 = 0x07;
        constexpr uint8_t kModeConfigReg08 = 0x08;
        constexpr uint8_t kTimeControl0Reg09 = 0x09;
        constexpr uint8_t kTimeControl1Reg0A = 0x0A;
        constexpr uint8_t kSdpInterface1Reg11 = 0x11;
        constexpr uint8_t kSdpInterface2Reg12 = 0x12;
        constexpr uint8_t kAdc34Hpf2Reg20 = 0x20;
        constexpr uint8_t kAdc34Hpf1Reg21 = 0x21;
        constexpr uint8_t kAdc12Hpf1Reg22 = 0x22;
        constexpr uint8_t kAdc12Hpf2Reg23 = 0x23;
        constexpr uint8_t kAnalogReg40 = 0x40;
        constexpr uint8_t kMic12BiasReg41 = 0x41;
        constexpr uint8_t kMic34BiasReg42 = 0x42;
        constexpr uint8_t kMic1GainReg43 = 0x43;
        constexpr uint8_t kMic2GainReg44 = 0x44;
        constexpr uint8_t kMic3GainReg45 = 0x45;
        constexpr uint8_t kMic1PowerReg47 = 0x47;
        constexpr uint8_t kMic2PowerReg48 = 0x48;
        constexpr uint8_t kMic3PowerReg49 = 0x49;
        constexpr uint8_t kMic4PowerReg4A = 0x4A;
        constexpr uint8_t kMic12PowerReg4B = 0x4B;
        constexpr uint8_t kMic34PowerReg4C = 0x4C;
        constexpr uint8_t kLastDumpedReg = 0x4C;

        // 16 kHz from a 4.096 MHz MCLK (256*fs, what ESP_I2S emits by default), taken
        // from the coefficient table in the Espressif es7210.c driver.
        constexpr uint8_t kMainClk16k = 0xC1; // dll<<7 | doubler<<6 | adc_div(1)
        constexpr uint8_t kOsr16k = 0x20;
        constexpr uint8_t kLrckDivH16k = 0x01; // 0x0100 = 256
        constexpr uint8_t kLrckDivL16k = 0x00;

        constexpr uint8_t kFirstAddress = 0x40;
        constexpr uint8_t kLastAddress = 0x43;
        constexpr uint8_t kMaxGainStep = 14;

        // This board drops the occasional I2C transaction. Bring-up is a read-modify-write
        // over a dozen registers, so a single silent flake would leave the codec half
        // configured and the recording silent for a reason nothing logs. Hence retries.
        constexpr uint8_t kI2cAttempts = 3;

        bool readOnce(Context& context, uint8_t reg, uint8_t& value) {
            context.wire.beginTransmission(context.address);
            context.wire.write(reg);
            if (context.wire.endTransmission(false) != 0) {
                return false;
            }
            if (context.wire.requestFrom(static_cast<int>(context.address), 1, 1) != 1) {
                return false;
            }
            value = context.wire.read();
            return true;
        }

        bool readRegister(Context& context, uint8_t reg, uint8_t& value) {
            for (uint8_t attempt = 0; attempt < kI2cAttempts; ++attempt) {
                if (readOnce(context, reg, value)) {
                    return true;
                }
            }
            return false;
        }

        bool writeRegister(Context& context, uint8_t reg, uint8_t value) {
            for (uint8_t attempt = 0; attempt < kI2cAttempts; ++attempt) {
                context.wire.beginTransmission(context.address);
                context.wire.write(reg);
                context.wire.write(value);
                if (context.wire.endTransmission(true) == 0) {
                    return true;
                }
            }
            ESP_LOGW(kTag, "write REG %02X failed", reg);
            return false;
        }

        bool updateBits(Context& context, uint8_t reg, uint8_t mask, uint8_t value) {
            uint8_t current = 0;
            if (!readRegister(context, reg, current)) {
                return false;
            }
            const auto merged = static_cast<uint8_t>((current & ~mask) | (value & mask));
            return writeRegister(context, reg, merged);
        }

        bool responds(Context& context) {
            context.wire.beginTransmission(context.address);
            return context.wire.endTransmission(true) == 0;
        }

        // Exactly one pair is enabled at a time. Two channels stays below the threshold
        // where the codec would need TDM, so the pair lands as the left and right slots
        // of plain I2S and the existing capture path needs no change.
        bool selectMicPair(Context& context, uint8_t gain, MicPair pair) {
            bool ok = true;
            // Clear the enable bit on every channel first, so a re-run cannot leave a
            // channel enabled that this configuration does not want.
            for (uint8_t offset = 0; offset < 4; ++offset) {
                ok = updateBits(context, static_cast<uint8_t>(kMic1GainReg43 + offset), 0x10, 0x00) && ok;
            }
            ok = writeRegister(context, kMic12PowerReg4B, 0xFF) && ok;
            ok = writeRegister(context, kMic34PowerReg4C, 0xFF) && ok;

            const bool low = pair == MicPair::Mic12;
            const uint8_t clockMask = low ? 0x0B : 0x15;
            const uint8_t powerReg = low ? kMic12PowerReg4B : kMic34PowerReg4C;
            const uint8_t firstGainReg = low ? kMic1GainReg43 : kMic3GainReg45;

            ok = updateBits(context, kClockOffReg01, clockMask, 0x00) && ok;
            ok = writeRegister(context, powerReg, 0x00) && ok;
            for (uint8_t offset = 0; offset < 2; ++offset) {
                const auto reg = static_cast<uint8_t>(firstGainReg + offset);
                ok = updateBits(context, reg, 0x10, 0x10) && ok;
                ok = updateBits(context, reg, 0x0F, gain) && ok;
            }
            ok = writeRegister(context, kSdpInterface2Reg12, 0x00) && ok;
            return ok;
        }

    } // namespace

    bool begin(Context& context) {
        if (context.available) {
            return true;
        }

        for (uint8_t address = kFirstAddress; address <= kLastAddress; ++address) {
            context.address = address;
            if (responds(context)) {
                ESP_LOGI(kTag, "ES7210 answering at 0x%02X", address);
                context.available = true;
                return true;
            }
        }

        context.address = kFirstAddress;
        ESP_LOGW(kTag, "ES7210 not found on 0x%02X..0x%02X", kFirstAddress, kLastAddress);
        return false;
    }

    bool prepareInput(Context& context, uint8_t gain, MicPair pair) {
        if (!begin(context)) {
            return false;
        }
        if (gain > kMaxGainStep) {
            gain = kMaxGainStep;
        }

        bool ok = true;
        ok = writeRegister(context, kResetReg00, 0xFF) && ok;
        ok = writeRegister(context, kResetReg00, 0x41) && ok;
        // The chip is coming out of a software reset; give it a moment before the
        // configuration writes rather than relying on the retry loop to absorb NACKs.
        delay(2);
        ok = writeRegister(context, kClockOffReg01, 0x3F) && ok;
        ok = writeRegister(context, kTimeControl0Reg09, 0x30) && ok;
        ok = writeRegister(context, kTimeControl1Reg0A, 0x30) && ok;
        ok = writeRegister(context, kAdc12Hpf2Reg23, 0x2A) && ok;
        ok = writeRegister(context, kAdc12Hpf1Reg22, 0x0A) && ok;
        ok = writeRegister(context, kAdc34Hpf2Reg20, 0x0A) && ok;
        ok = writeRegister(context, kAdc34Hpf1Reg21, 0x2A) && ok;
        // Slave: the ESP32 drives MCLK, BCLK and LRCK.
        ok = updateBits(context, kModeConfigReg08, 0x01, 0x00) && ok;
        ok = writeRegister(context, kAnalogReg40, 0x43) && ok;    // vdda 3.3 V, VMID 5k start
        ok = writeRegister(context, kMic12BiasReg41, 0x70) && ok; // 2.87 V microphone bias
        ok = writeRegister(context, kMic34BiasReg42, 0x70) && ok;
        ok = writeRegister(context, kOsrReg07, kOsr16k) && ok;
        ok = writeRegister(context, kMainClkReg02, kMainClk16k) && ok;
        ok = writeRegister(context, kLrckDivHReg04, kLrckDivH16k) && ok;
        ok = writeRegister(context, kLrckDivLReg05, kLrckDivL16k) && ok;
        // 16-bit words (bits 7:5 = 0x60) and plain I2S framing (bits 1:0 = 0).
        ok = updateBits(context, kSdpInterface1Reg11, 0xE3, 0x60) && ok;

        // Power up. Without this every register reads back correctly and the chip still
        // sends nothing but zeros.
        ok = writeRegister(context, kPowerDownReg06, 0x00) && ok;
        ok = writeRegister(context, kMic1PowerReg47, 0x08) && ok;
        ok = writeRegister(context, kMic2PowerReg48, 0x08) && ok;
        ok = writeRegister(context, kMic3PowerReg49, 0x08) && ok;
        ok = writeRegister(context, kMic4PowerReg4A, 0x08) && ok;
        ok = selectMicPair(context, gain, pair) && ok;
        // Re-assert the analog block and kick the ADC out of reset. The esp-adf driver
        // stops after the mic selection; the current esp_codec_dev one ends exactly
        // here, and without these three writes the front end comes up alive but roughly
        // 40 dB down -- measured on this board, not guessed.
        ok = writeRegister(context, kAnalogReg40, 0x43) && ok;
        ok = writeRegister(context, kResetReg00, 0x71) && ok;
        ok = writeRegister(context, kResetReg00, 0x41) && ok;

        if (!ok) {
            ESP_LOGW(kTag, "ES7210 configuration incomplete");
        } else {
            ESP_LOGI(kTag, "ES7210 ready: 16 kHz, %s, gain step %u",
                     pair == MicPair::Mic12 ? "MIC1+MIC2" : "MIC3+MIC4", gain);
        }
        return ok;
    }

    void dumpRegisters(Context& context) {
        if (!context.available) {
            ESP_LOGW(kTag, "ES7210 unavailable, nothing to dump");
            return;
        }
        for (uint8_t reg = 0x00; reg <= kLastDumpedReg; ++reg) {
            uint8_t value = 0;
            if (readRegister(context, reg, value)) {
                ESP_LOGI(kTag, "REG %02X = %02X", reg, value);
            } else {
                ESP_LOGW(kTag, "REG %02X read failed", reg);
            }
        }
    }

    bool available(const Context& context) {
        return context.available;
    }

    void scanBus(TwoWire& wire) {
        ESP_LOGI(kTag, "I2C scan start");
        for (uint8_t address = 0x08; address < 0x78; ++address) {
            wire.beginTransmission(address);
            if (wire.endTransmission(true) == 0) {
                ESP_LOGI(kTag, "I2C device at 0x%02X", address);
            }
        }
        ESP_LOGI(kTag, "I2C scan done");
    }

} // namespace BoardDrivers::Es7210
