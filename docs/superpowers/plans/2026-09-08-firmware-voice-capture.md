# Firmware: captura de áudio (plano 2a)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gravar áudio do microfone da placa em WAV 16 kHz mono 16-bit no cartão SD, produzindo um arquivo que se pluga no PC e se ouve.

**Architecture:** Três camadas, seguindo a abstração de placa que o autor já estabeleceu. O driver `Es8311` ganha um caminho de entrada ao lado do de saída que já existe; o adaptador compartilhado `Es8311BoardAudio` expõe isso para as placas que usam o codec; e um módulo novo `src/voice/` faz a orquestração. O `WavWriter` é lógica pura, construído sobre o `BufferedWriter` existente, e por isso é testável no PC sem hardware.

**Tech Stack:** C++23, Arduino-ESP32 3.3.9, `ESP_I2S`, PlatformIO, Unity para testes nativos.

**Spec:** [`docs/superpowers/specs/2026-09-07-notas-de-voz-obsidian-design.md`](../specs/2026-09-07-notas-de-voz-obsidian-design.md)

## Global Constraints

- Placa: `waveshare_esp32s3_touch_lcd_349_rev2`. Pinos de áudio verificados em `WaveshareLcd349.h`: MCLK=7, BCLK=15, WS=46, **DIN=6**, DOUT=45, codec ES8311 em `0x18` no `Wire1`.
- **Sempre rodar `pio` pelo PowerShell, nunca Git Bash** — o `idf_tools.py` aborta sob MSYS. Ver a seção 3.1 da spec.
- Formato de áudio fixo: **16 kHz, mono, 16-bit PCM**. É o que o `Es8311::Context` já usa por padrão e o que o Handy exige.
- O `WavWriter` **não pode depender da `ESP_I2S`**. O `wav_header.h` daquela biblioteca não é compilado no env `native_test`, então o cabeçalho é montado à mão. (Isto corrige o que a spec dizia originalmente.)
- Toda função nova de I/O retorna `std::expected<void, std::error_code>`, seguindo a convenção do repositório.
- Código, símbolos e mensagens de commit em inglês.
- **Ripple mínimo:** declarar as funções novas em `src/board/BoardAudio.h` não obriga as outras cinco plataformas a implementá-las, porque nada nos builds delas as chama. Só o forwarder do `waveshare_lcd_349` é escrito agora.

---

### Task 1: `WavWriter` — cabeçalho e contabilidade, testável no PC

**Files:**
- Create: `src/voice/WavWriter.h`
- Create: `src/voice/WavWriter.cpp`
- Create: `test/test_wav_writer/test_main.cpp`
- Modify: `platformio.ini` (adicionar ao `test_filter` e ao `build_src_filter` do `native_test`)

**Files (adicional):**
- Modify: `test/support/FS.h` (corrigir a semântica de `write`)

**Interfaces:**
- Consumes: `BufferedWriter` de `src/storage/fs/BufferedWriter.h` — verificado: seu `seek()` faz `flush()` antes de posicionar, então corrigir o cabeçalho no `finish()` é seguro. Shim `File` de `test/support/FS.h`, que expõe `contents()` devolvendo `const std::string&`.
- Produces: `voice::WavWriter` com `begin()`, `writeSamples(const int16_t*, size_t)`, `finish()`, `frameCount()`, `durationMs()`; constantes `kSampleRateHz`, `kChannels`, `kBitsPerSample`, `kHeaderBytes`

- [ ] **Step 0: Corrigir o `write` do shim de teste — obrigatório antes de tudo**

O `write` de `test/support/FS.h` hoje **ignora a posição** e sempre acrescenta no fim:

```cpp
  size_t write(const uint8_t *data, size_t size) {
    if (data == nullptr) return 0;
    data_->append(reinterpret_cast<const char *>(data), size);
    position_ = data_->size();      // <-- descarta qualquer seek anterior
    return size;
  }
```

Com isso, corrigir o cabeçalho no `finish()` acrescentaria 8 bytes no fim do arquivo em vez de sobrescrever os offsets 4 e 40 — e o teste de tamanhos falharia por um motivo que não tem nada a ver com o `WavWriter`. Substituir por semântica real de arquivo:

```cpp
  size_t write(const uint8_t *data, size_t size) {
    if (data == nullptr) return 0;
    if (position_ > data_->size()) return 0;
    // Overwrite in place from the current position, extending only past the end.
    // The previous version always appended, which made seek-then-write silently wrong.
    const size_t overwrite = std::min(size, data_->size() - position_);
    if (overwrite > 0) {
      std::memcpy(data_->data() + position_, data, overwrite);
    }
    if (overwrite < size) {
      data_->append(reinterpret_cast<const char *>(data) + overwrite, size - overwrite);
    }
    position_ += size;
    return size;
  }
```

Quando `position_` já está no fim — o caso comum — o comportamento é idêntico ao anterior, então os testes que só acrescentam continuam válidos. Mas **este shim é compartilhado por vários testes do repositório**, então o Step 8 (suíte completa) deixa de ser formalidade e passa a ser o gate desta mudança.

- [ ] **Step 1: Escrever o teste que falha**

```cpp
// test/test_wav_writer/test_main.cpp
#include <unity.h>

#include <cstdint>
#include <cstring>
#include <vector>

#include <FS.h>

#include "voice/WavWriter.h"

namespace {

    // O shim de teste expoe contents() como std::string, nao um vetor de bytes.
    uint8_t byteAt(const std::string& b, size_t at) {
        return static_cast<uint8_t>(b[at]);
    }
    uint32_t le32(const std::string& b, size_t at) {
        return static_cast<uint32_t>(byteAt(b, at)) | (static_cast<uint32_t>(byteAt(b, at + 1)) << 8)
            | (static_cast<uint32_t>(byteAt(b, at + 2)) << 16) | (static_cast<uint32_t>(byteAt(b, at + 3)) << 24);
    }
    uint16_t le16(const std::string& b, size_t at) {
        return static_cast<uint16_t>(static_cast<uint16_t>(byteAt(b, at))
                                     | (static_cast<uint16_t>(byteAt(b, at + 1)) << 8));
    }
    bool tagAt(const std::string& b, size_t at, const char* tag) {
        return std::memcmp(b.data() + at, tag, 4) == 0;
    }

    void test_header_describes_16k_mono_16bit() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());
        TEST_ASSERT_TRUE(writer.finish().has_value());

        const std::string& b = file.contents();
        TEST_ASSERT_EQUAL_UINT32(voice::WavWriter::kHeaderBytes, b.size());
        TEST_ASSERT_TRUE(tagAt(b, 0, "RIFF"));
        TEST_ASSERT_TRUE(tagAt(b, 8, "WAVE"));
        TEST_ASSERT_TRUE(tagAt(b, 12, "fmt "));
        TEST_ASSERT_EQUAL_UINT32(16, le32(b, 16));      // fmt canonico de 16 bytes
        TEST_ASSERT_EQUAL_UINT16(1, le16(b, 20));       // PCM
        TEST_ASSERT_EQUAL_UINT16(1, le16(b, 22));       // mono
        TEST_ASSERT_EQUAL_UINT32(16000, le32(b, 24));
        TEST_ASSERT_EQUAL_UINT32(32000, le32(b, 28));   // byteRate
        TEST_ASSERT_EQUAL_UINT16(2, le16(b, 32));       // blockAlign
        TEST_ASSERT_EQUAL_UINT16(16, le16(b, 34));
        TEST_ASSERT_TRUE(tagAt(b, 36, "data"));
    }

    void test_finish_patches_both_sizes() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());

        std::vector<int16_t> samples(16000, 1234);      // 1 segundo
        TEST_ASSERT_TRUE(writer.writeSamples(samples.data(), samples.size()).has_value());
        TEST_ASSERT_TRUE(writer.finish().has_value());

        const std::string& b = file.contents();
        const uint32_t payload = 16000 * 2;
        TEST_ASSERT_EQUAL_UINT32(voice::WavWriter::kHeaderBytes + payload, b.size());
        TEST_ASSERT_EQUAL_UINT32(36 + payload, le32(b, 4));    // tamanho RIFF
        TEST_ASSERT_EQUAL_UINT32(payload, le32(b, 40));        // tamanho data
    }

    void test_samples_are_written_little_endian_in_order() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());
        const int16_t samples[] = {0x0102, static_cast<int16_t>(0xFF7F), 0x0000};
        TEST_ASSERT_TRUE(writer.writeSamples(samples, 3).has_value());
        TEST_ASSERT_TRUE(writer.finish().has_value());

        const std::string& b = file.contents();
        const size_t at = voice::WavWriter::kHeaderBytes;
        TEST_ASSERT_EQUAL_UINT8(0x02, b[at + 0]);
        TEST_ASSERT_EQUAL_UINT8(0x01, b[at + 1]);
        TEST_ASSERT_EQUAL_UINT8(0x7F, b[at + 2]);
        TEST_ASSERT_EQUAL_UINT8(0xFF, b[at + 3]);
    }

    void test_counts_frames_and_duration() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());
        std::vector<int16_t> samples(8000, 0);          // meio segundo
        TEST_ASSERT_TRUE(writer.writeSamples(samples.data(), samples.size()).has_value());
        TEST_ASSERT_EQUAL_UINT32(8000, writer.frameCount());
        TEST_ASSERT_EQUAL_UINT32(500, writer.durationMs());
    }

    void test_many_small_writes_match_one_big_write() {
        File a;
        voice::WavWriter wa(a);
        TEST_ASSERT_TRUE(wa.begin().has_value());
        std::vector<int16_t> block(1024);
        for (size_t i = 0; i < block.size(); ++i)
            block[i] = static_cast<int16_t>(i);
        for (int n = 0; n < 16; ++n)
            TEST_ASSERT_TRUE(wa.writeSamples(block.data(), block.size()).has_value());
        TEST_ASSERT_TRUE(wa.finish().has_value());

        File b;
        voice::WavWriter wb(b);
        TEST_ASSERT_TRUE(wb.begin().has_value());
        std::vector<int16_t> all;
        for (int n = 0; n < 16; ++n)
            all.insert(all.end(), block.begin(), block.end());
        TEST_ASSERT_TRUE(wb.writeSamples(all.data(), all.size()).has_value());
        TEST_ASSERT_TRUE(wb.finish().has_value());

        TEST_ASSERT_EQUAL_UINT32(a.contents().size(), b.contents().size());
        TEST_ASSERT_EQUAL_INT(0, std::memcmp(a.contents().data(), b.contents().data(), a.contents().size()));
    }

    void test_write_without_begin_fails() {
        File file;
        voice::WavWriter writer(file);
        const int16_t sample = 0;
        TEST_ASSERT_FALSE(writer.writeSamples(&sample, 1).has_value());
    }

    void test_zero_length_recording_still_produces_valid_header() {
        File file;
        voice::WavWriter writer(file);
        TEST_ASSERT_TRUE(writer.begin().has_value());
        TEST_ASSERT_TRUE(writer.finish().has_value());
        TEST_ASSERT_EQUAL_UINT32(0, le32(file.contents(), 40));
        TEST_ASSERT_EQUAL_UINT32(0, writer.frameCount());
    }

} // namespace

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_header_describes_16k_mono_16bit);
    RUN_TEST(test_finish_patches_both_sizes);
    RUN_TEST(test_samples_are_written_little_endian_in_order);
    RUN_TEST(test_counts_frames_and_duration);
    RUN_TEST(test_many_small_writes_match_one_big_write);
    RUN_TEST(test_write_without_begin_fails);
    RUN_TEST(test_zero_length_recording_still_produces_valid_header);
    return UNITY_END();
}
```

- [ ] **Step 2: Verificar o que o shim `File` de teste já oferece**

Run: `grep -nE "class File|bytes|write|seek|size" test/support/FS.h`

O teste acima assume que o shim expõe `bytes()` devolvendo o conteúdo escrito e suporta `write`/`seek`. Se não expuser, **estenda o shim** em `test/support/FS.h` com o mínimo necessário — é código de teste, não de produção, e outros testes do repositório já dependem dele.

- [ ] **Step 3: Registrar o teste no `platformio.ini`**

No env `native_test`, acrescentar ao `test_filter`:

```ini
  test_wav_writer
```

e ao `build_src_filter`:

```ini
  +<voice/WavWriter.cpp>
```

- [ ] **Step 4: Rodar e confirmar que falha**

Run (PowerShell):
```powershell
$env:Path = "<mingw64\bin>;C:\Users\kakam\.local\bin;$env:Path"
pio test -e native_test -f test_wav_writer
```
Expected: FAIL na compilação — `voice/WavWriter.h: No such file or directory`

- [ ] **Step 5: Escrever o header**

```cpp
// src/voice/WavWriter.h
#pragma once

#include <FS.h>

#include <cstddef>
#include <cstdint>
#include <expected>
#include <system_error>

#include "storage/fs/BufferedWriter.h"

namespace voice {

    // Writes a canonical 44-byte-header PCM WAV. Deliberately independent of ESP_I2S's
    // wav_header.h so this stays compilable and testable in the native test env.
    class WavWriter {
    public:
        static constexpr uint32_t kSampleRateHz = 16000;
        static constexpr uint16_t kChannels = 1;
        static constexpr uint16_t kBitsPerSample = 16;
        static constexpr size_t kHeaderBytes = 44;

        explicit WavWriter(File& file);

        // Reserves the header with zeroed sizes so samples can stream straight after.
        std::expected<void, std::error_code> begin();
        std::expected<void, std::error_code> writeSamples(const int16_t* samples, size_t count);
        // Flushes, seeks back and patches the RIFF and data sizes.
        std::expected<void, std::error_code> finish();

        [[nodiscard]] uint32_t frameCount() const { return frames_; }
        [[nodiscard]] uint32_t durationMs() const {
            return static_cast<uint32_t>((static_cast<uint64_t>(frames_) * 1000U) / kSampleRateHz);
        }

    private:
        File& file_;
        BufferedWriter writer_;
        uint32_t frames_ = 0;
        bool started_ = false;
        bool finished_ = false;
    };

} // namespace voice
```

- [ ] **Step 6: Escrever a implementação**

```cpp
// src/voice/WavWriter.cpp
#include "voice/WavWriter.h"

#include <array>
#include <cstring>

namespace voice {

    namespace {

        constexpr uint32_t kByteRate = WavWriter::kSampleRateHz * WavWriter::kChannels
            * (WavWriter::kBitsPerSample / 8U);
        constexpr uint16_t kBlockAlign = WavWriter::kChannels * (WavWriter::kBitsPerSample / 8U);
        constexpr uint32_t kRiffSizeOffset = 4;
        constexpr uint32_t kDataSizeOffset = 40;

        void put32(uint8_t* at, uint32_t value) {
            at[0] = static_cast<uint8_t>(value & 0xFFU);
            at[1] = static_cast<uint8_t>((value >> 8) & 0xFFU);
            at[2] = static_cast<uint8_t>((value >> 16) & 0xFFU);
            at[3] = static_cast<uint8_t>((value >> 24) & 0xFFU);
        }
        void put16(uint8_t* at, uint16_t value) {
            at[0] = static_cast<uint8_t>(value & 0xFFU);
            at[1] = static_cast<uint8_t>((value >> 8) & 0xFFU);
        }

        std::array<uint8_t, WavWriter::kHeaderBytes> makeHeader(uint32_t dataBytes) {
            std::array<uint8_t, WavWriter::kHeaderBytes> h{};
            std::memcpy(h.data() + 0, "RIFF", 4);
            put32(h.data() + 4, 36U + dataBytes);
            std::memcpy(h.data() + 8, "WAVE", 4);
            std::memcpy(h.data() + 12, "fmt ", 4);
            put32(h.data() + 16, 16U);                          // canonical fmt chunk
            put16(h.data() + 20, 1U);                           // PCM
            put16(h.data() + 22, WavWriter::kChannels);
            put32(h.data() + 24, WavWriter::kSampleRateHz);
            put32(h.data() + 28, kByteRate);
            put16(h.data() + 32, kBlockAlign);
            put16(h.data() + 34, WavWriter::kBitsPerSample);
            std::memcpy(h.data() + 36, "data", 4);
            put32(h.data() + 40, dataBytes);
            return h;
        }

    } // namespace

    WavWriter::WavWriter(File& file) : file_(file), writer_(file) {}

    std::expected<void, std::error_code> WavWriter::begin() {
        if (started_) {
            return std::unexpected(std::make_error_code(std::errc::operation_in_progress));
        }
        const auto header = makeHeader(0);
        if (auto written = writer_.write(header.data(), header.size()); !written) {
            return written;
        }
        started_ = true;
        return {};
    }

    std::expected<void, std::error_code> WavWriter::writeSamples(const int16_t* samples, size_t count) {
        if (!started_ || finished_) {
            return std::unexpected(std::make_error_code(std::errc::operation_not_permitted));
        }
        if (count == 0) {
            return {};
        }
        // int16_t is already little-endian on xtensa and on the native test host, so the
        // sample block goes out verbatim rather than byte by byte.
        if (auto written = writer_.write(samples, count * sizeof(int16_t)); !written) {
            return written;
        }
        frames_ += static_cast<uint32_t>(count / kChannels);
        return {};
    }

    std::expected<void, std::error_code> WavWriter::finish() {
        if (!started_ || finished_) {
            return std::unexpected(std::make_error_code(std::errc::operation_not_permitted));
        }
        if (auto flushed = writer_.flush(); !flushed) {
            return flushed;
        }

        const uint32_t dataBytes = frames_ * kBlockAlign;
        const auto header = makeHeader(dataBytes);

        // Patch only the two size fields; never rewrite the chunk identifiers.
        if (auto sought = writer_.seek(kRiffSizeOffset); !sought) {
            return sought;
        }
        if (auto written = writer_.write(header.data() + kRiffSizeOffset, 4); !written) {
            return written;
        }
        if (auto sought = writer_.seek(kDataSizeOffset); !sought) {
            return sought;
        }
        if (auto written = writer_.write(header.data() + kDataSizeOffset, 4); !written) {
            return written;
        }
        if (auto flushed = writer_.flush(); !flushed) {
            return flushed;
        }
        finished_ = true;
        return {};
    }

} // namespace voice
```

- [ ] **Step 7: Rodar e confirmar que passa**

Run: `pio test -e native_test -f test_wav_writer`
Expected: PASS (7 testes)

- [ ] **Step 8: Rodar a suíte inteira, para garantir que o `platformio.ini` não quebrou nada**

Run: `pio test -e native_test`
Expected: 203 casos (196 anteriores + 7 novos), todos passando

- [ ] **Step 9: Commit**

```bash
git add src/voice/WavWriter.h src/voice/WavWriter.cpp test/test_wav_writer/ platformio.ini
git commit -m "feat(voice): add WavWriter for 16 kHz mono PCM on the SD card"
```

---

### Task 2: Caminho de entrada do ES8311

Aqui o full-duplex acontece. O `din` está fixado em `-1` em `Es8311.cpp:70`; passar o pino real faz a `ESP_I2S` alocar `rx_chan` junto com `tx_chan` automaticamente (`ESP_I2S.cpp:505-509`), sem alternância de modo e sem risco para o `beep()`.

**Files:**
- Modify: `src/drivers/audio/es8311/Es8311.h` (campo `dataInPin` no `Context`, duas declarações)
- Modify: `src/drivers/audio/es8311/Es8311.cpp` (passar `din` no `setPins`, `prepareInput`, `readSamples`)
- Modify: `src/platforms/common/Es8311BoardAudio.h` (dois wrappers)
- Modify: `src/board/BoardAudio.h` (três declarações)
- Modify: `src/platforms/waveshare_lcd_349/BoardAudio.cpp` (contexto com `kDinPin` + forwarders)

**Interfaces:**
- Consumes: nada de tasks anteriores
- Produces: `Board::Audio::prepareInput() -> bool`, `Board::Audio::readSamples(int16_t*, size_t, uint32_t) -> size_t`, `Board::Audio::inputAvailable() -> bool`

- [ ] **Step 1: Adicionar `dataInPin` ao `Context` e declarar as funções**

Em `Es8311.h`, o construtor ganha o parâmetro **antes** de `sampleRateHz` para manter a chamada existente compilando por posição apenas onde já é nomeada — na prática todas as plataformas passam posicionalmente, então acrescente ao final com valor padrão:

```cpp
        Context(TwoWire& wire, uint8_t address, i2s_port_t i2sPort, int mclkPin, int bclkPin, int wsPin,
                int dataOutPin, uint32_t sampleRateHz = 16000, int dataInPin = -1) :
                wire(wire), address(address), i2sPort(i2sPort), i2s(i2sPort), mclkPin(mclkPin),
                bclkPin(bclkPin), wsPin(wsPin), dataOutPin(dataOutPin), dataInPin(dataInPin),
                sampleRateHz(sampleRateHz) {}
```

Acrescentar o campo junto aos outros e, ao lado de `prepareOutput`/`writeSamples`:

```cpp
        int dataInPin = -1;
```

```cpp
    bool prepareInput(Context& context);
    size_t readSamples(Context& context, int16_t* samples, size_t sampleCount, uint32_t timeoutMs);
```

**Nota:** `dataInPin` entra com padrão `-1`, então as outras cinco plataformas continuam compilando sem alteração e continuam em TX-only, exatamente como hoje.

- [ ] **Step 2: Passar o pino de entrada no `setPins`**

Em `Es8311.cpp`, linha 70, trocar o `-1` fixo:

```cpp
            context.i2s.setPins(context.bclkPin, context.wsPin, context.dataOutPin, context.dataInPin,
                                context.mclkPin);
```

- [ ] **Step 3: Implementar `prepareInput` e `readSamples`**

O `configureCodec` já escreve os registradores de ADC (`kAdcReg15/16/17/1B/1C`, `kSystemReg14`) e a interface `adcIface`, então `prepareInput` só precisa garantir a inicialização e destravar o ganho de entrada:

```cpp
    bool prepareInput(Context& context) {
        if (context.dataInPin < 0) {
            ESP_LOGW(kTag, "no input pin configured for this board");
            return false;
        }
        if (!begin(context)) {
            return false;
        }
        // ADC path and PGA are already configured by configureCodec(); this only
        // unmutes and sets the microphone gain to the tuned value.
        return writeRegister(context, kAdcReg17, kAdcVolumeMax)
            && writeRegister(context, kSystemReg14, kMicPgaGain);
    }

    size_t readSamples(Context& context, int16_t* samples, size_t sampleCount, uint32_t timeoutMs) {
        if (!available(context) || samples == nullptr || sampleCount == 0) {
            return 0;
        }
        const size_t wanted = sampleCount * sizeof(int16_t);
        const size_t got = context.i2s.readBytes(reinterpret_cast<char*>(samples), wanted);
        (void) timeoutMs;   // ESP_I2S::readBytes blocks on its own configured timeout
        return got / sizeof(int16_t);
    }
```

Acrescentar as duas constantes junto às outras, no bloco anônimo do topo do arquivo:

```cpp
        constexpr uint8_t kAdcVolumeMax = 0xBF;
        // Bits 3:0 are the analog microphone PGA gain. 0x1A is the value configureCodec
        // already writes; Task 5 tunes this against real recordings.
        constexpr uint8_t kMicPgaGain = 0x1A;
```

- [ ] **Step 4: Expor no adaptador compartilhado**

Em `src/platforms/common/Es8311BoardAudio.h`, dentro do namespace `BoardPlatform::Es8311BoardAudio`:

```cpp
    bool prepareInput(BoardDrivers::Es8311::Context& context) {
        if (!enableAudioRail()) {
            ESP_LOGW(kAudioTag, "Audio rail unavailable");
            return false;
        }
        delay(kAudioStartupDelayMs);
        return BoardDrivers::Es8311::prepareInput(context);
    }

    size_t readSamples(BoardDrivers::Es8311::Context& context, int16_t* samples, size_t sampleCount,
                       uint32_t timeoutMs) {
        return BoardDrivers::Es8311::readSamples(context, samples, sampleCount, timeoutMs);
    }
```

- [ ] **Step 5: Declarar na interface de placa**

Em `src/board/BoardAudio.h`:

```cpp
    // Capture is implemented only on boards wired for it; see the note in the plan's
    // global constraints about why the other platforms need no change.
    bool prepareInput();
    size_t readSamples(int16_t* samples, size_t sampleCount, uint32_t timeoutMs);
    bool inputAvailable();
```

- [ ] **Step 6: Implementar no forwarder do 3.49**

Em `src/platforms/waveshare_lcd_349/BoardAudio.cpp`, acrescentar `kDinPin` ao contexto e os três forwarders:

```cpp
    BoardDrivers::Es8311::Context gAudioContext = {
        Wire1,
        WaveshareLcd349::AudioWiring::kEs8311Address,
        I2S_NUM_0,
        WaveshareLcd349::AudioWiring::kMclkPin,
        WaveshareLcd349::AudioWiring::kBclkPin,
        WaveshareLcd349::AudioWiring::kWsPin,
        WaveshareLcd349::AudioWiring::kDoutPin,
        16000,
        WaveshareLcd349::AudioWiring::kDinPin,
    };
```

```cpp
    bool prepareInput() {
        return BoardPlatform::Es8311BoardAudio::prepareInput(gAudioContext);
    }

    size_t readSamples(int16_t* samples, size_t sampleCount, uint32_t timeoutMs) {
        return BoardPlatform::Es8311BoardAudio::readSamples(gAudioContext, samples, sampleCount, timeoutMs);
    }

    bool inputAvailable() {
        return WaveshareLcd349::AudioWiring::kDinPin >= 0
            && BoardPlatform::Es8311BoardAudio::available(gAudioContext);
    }
```

- [ ] **Step 7: Compilar e confirmar que nada quebrou**

Run (PowerShell):
```powershell
pio run -e waveshare_esp32s3_touch_lcd_349_rev2
pio run -e waveshare_esp32s3_touch_amoled_216
```
Expected: SUCCESS nos dois. O segundo prova que as plataformas não tocadas continuam compilando.

- [ ] **Step 8: Commit**

```bash
git add src/drivers/audio/es8311/ src/platforms/common/Es8311BoardAudio.h src/board/BoardAudio.h src/platforms/waveshare_lcd_349/BoardAudio.cpp
git commit -m "feat(audio): add an ES8311 capture path alongside playback"
```

---

### Task 3: `VoiceCapture` — orquestração da gravação

**Files:**
- Create: `src/voice/VoiceCapture.h`
- Create: `src/voice/VoiceCapture.cpp`

**Interfaces:**
- Consumes: `voice::WavWriter` (Task 1), `Board::Audio::prepareInput`/`readSamples` (Task 2), `Board::Storage::filesystem()`
- Produces: `voice::CaptureResult { bool ok; uint32_t durationMs; uint32_t framesWritten; const char* error; }`, `voice::captureToFile(const char* path, uint32_t maxDurationMs) -> CaptureResult`

- [ ] **Step 1: Escrever o header**

```cpp
// src/voice/VoiceCapture.h
#pragma once

#include <cstdint>

namespace voice {

    struct CaptureResult {
        bool ok = false;
        uint32_t durationMs = 0;
        uint32_t framesWritten = 0;
        const char* error = nullptr;
    };

    // Records from the board microphone straight into a WAV on the SD card.
    // Blocking: intended for a dedicated task, never for the UI loop.
    CaptureResult captureToFile(const char* path, uint32_t maxDurationMs);

} // namespace voice
```

- [ ] **Step 2: Escrever a implementação**

```cpp
// src/voice/VoiceCapture.cpp
#include "voice/VoiceCapture.h"

#include <Arduino.h>
#include <esp_heap_caps.h>
#include <esp_log.h>

#include "board/BoardAudio.h"
#include "board/BoardStorage.h"
#include "voice/WavWriter.h"

namespace voice {

    namespace {

        constexpr char kTag[] = "voice";
        // 16 kHz mono 16-bit is 32 KB/s, so each half of the ping-pong buffer holds
        // roughly 0.5 s of audio: enough slack for an SD write to complete while the
        // other half fills.
        constexpr size_t kBlockSamples = 8192;

        int16_t* allocateBlock() {
            void* p = heap_caps_malloc(kBlockSamples * sizeof(int16_t), MALLOC_CAP_SPIRAM);
            if (p == nullptr) {
                p = heap_caps_malloc(kBlockSamples * sizeof(int16_t), MALLOC_CAP_DEFAULT);
            }
            return static_cast<int16_t*>(p);
        }

    } // namespace

    CaptureResult captureToFile(const char* path, uint32_t maxDurationMs) {
        CaptureResult result;

        if (!Board::Audio::prepareInput()) {
            result.error = "microphone unavailable";
            return result;
        }

        int16_t* block = allocateBlock();
        if (block == nullptr) {
            result.error = "out of memory";
            return result;
        }

        File file = Board::Storage::filesystem().open(path, FILE_WRITE);
        if (!file) {
            heap_caps_free(block);
            result.error = "could not open file";
            return result;
        }

        WavWriter writer(file);
        if (auto begun = writer.begin(); !begun) {
            file.close();
            heap_caps_free(block);
            result.error = "could not write header";
            return result;
        }

        const uint32_t startedAt = millis();
        const char* failure = nullptr;
        while ((millis() - startedAt) < maxDurationMs) {
            const size_t read = Board::Audio::readSamples(block, kBlockSamples, 1000);
            if (read == 0) {
                failure = "microphone read returned nothing";
                break;
            }
            if (auto written = writer.writeSamples(block, read); !written) {
                failure = "SD write failed";
                break;
            }
        }

        // finish() runs even after a failure: a partial recording with a correct header
        // is far more useful than a file the bridge has to repair.
        const auto finished = writer.finish();
        file.close();
        heap_caps_free(block);

        result.framesWritten = writer.frameCount();
        result.durationMs = writer.durationMs();
        if (failure != nullptr) {
            result.error = failure;
            ESP_LOGW(kTag, "capture ended early: %s (%u ms kept)", failure, result.durationMs);
            return result;
        }
        if (!finished) {
            result.error = "could not patch header";
            return result;
        }
        result.ok = true;
        ESP_LOGI(kTag, "captured %u ms to %s", result.durationMs, path);
        return result;
    }

} // namespace voice
```

- [ ] **Step 3: Compilar**

Run: `pio run -e waveshare_esp32s3_touch_lcd_349_rev2`
Expected: SUCCESS

- [ ] **Step 4: Commit**

```bash
git add src/voice/VoiceCapture.h src/voice/VoiceCapture.cpp
git commit -m "feat(voice): record from the microphone into a WAV on the card"
```

---

### Task 4: Env de autoteste, para poder validar no hardware

O objetivo desta task é ter **algo gravável e ouvível** sem construir UI nenhuma. É um andaime descartável, removido no plano 2b quando o gatilho real (duplo clique no BOOT) existir.

**Files:**
- Modify: `platformio.ini` (env novo `voice_selftest_waveshare_esp32s3_touch_lcd_349_rev2`)
- Modify: `src/app/App.cpp` (um bloco guardado por `#if`)

- [ ] **Step 1: Criar o env dedicado**

Copiar o env `waveshare_esp32s3_touch_lcd_349_rev2` inteiro, mudando apenas o nome e acrescentando a flag:

```ini
;
; Autoteste de captura de voz — andaime descartavel do plano 2a
;
[env:voice_selftest_waveshare_esp32s3_touch_lcd_349_rev2]
build_src_filter = ${common.lcd_349_src_filter}
build_flags =
  ${env.build_flags}
  ${common.usb_msc_build_flags}
  -DRSVP_LCD_349_REVISION_HEADER=\"platforms/waveshare_lcd_349/rev2/WaveshareLcd349Revision.h\"
  -DRSVP_BOARD_CONFIG_HEADER=\"platforms/waveshare_lcd_349/BoardConfig.h\"
  -DESP32QSPI_MAX_PIXELS_AT_ONCE=2048
  -DRSVP_VOICE_SELFTEST=1
build_unflags =
  ${env.build_unflags}
  ${common.usb_msc_build_unflags}
```

- [ ] **Step 2: Adicionar o gancho no `App.cpp`**

Localizar a chamada `Board::Audio::beep()` na linha ~158, que roda depois de o armazenamento estar montado, e inserir logo abaixo:

```cpp
#if defined(RSVP_VOICE_SELFTEST) && RSVP_VOICE_SELFTEST
            {
                // Throwaway scaffold from plan 2a: records ten seconds at boot so the
                // I2S full-duplex path, the microphone gain and the SD throughput can be
                // judged from a file you can actually listen to. Removed in plan 2b.
                const auto capture = voice::captureToFile("/voice-selftest.wav", 10000);
                ESP_LOGI("voice", "selftest ok=%d ms=%u frames=%u error=%s", capture.ok,
                         capture.durationMs, capture.framesWritten,
                         capture.error != nullptr ? capture.error : "none");
                Board::Audio::beep();   // proves playback still works after capturing
            }
#endif
```

E o include no topo do arquivo:

```cpp
#if defined(RSVP_VOICE_SELFTEST) && RSVP_VOICE_SELFTEST
#include "voice/VoiceCapture.h"
#endif
```

- [ ] **Step 3: Compilar os dois envs**

Run (PowerShell):
```powershell
pio run -e waveshare_esp32s3_touch_lcd_349_rev2
pio run -e voice_selftest_waveshare_esp32s3_touch_lcd_349_rev2
```
Expected: SUCCESS nos dois. O primeiro prova que o build normal não carrega o andaime.

- [ ] **Step 4: Rodar a suíte nativa**

Run: `pio test -e native_test`
Expected: 203 casos passando

- [ ] **Step 5: Commit**

```bash
git add platformio.ini src/app/App.cpp
git commit -m "build(voice): add a throwaway self-test env for capture bring-up"
```

---

### Task 5: Validação no hardware — exige a placa conectada

**Esta task não pode ser executada sem o aparelho ligado por USB.** Tudo antes dela é compilável e testável sozinho; nada aqui é.

- [ ] **Step 1: Confirmar que a placa apareceu**

Run (PowerShell):
```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.Name -match 'COM\d+' } | Select-Object Name
```
Expected: uma porta que **não** seja "Serial Padrão por link Bluetooth". Se só aparecerem as de Bluetooth, o cabo é de carga e não de dados — o README do projeto avisa sobre isso.

- [ ] **Step 2: Gravar o firmware de autoteste**

Run: `pio run -e voice_selftest_waveshare_esp32s3_touch_lcd_349_rev2 -t upload`

- [ ] **Step 3: Ler o log do boot enquanto fala perto do aparelho**

Run: `pio device monitor -e voice_selftest_waveshare_esp32s3_touch_lcd_349_rev2`

Fale continuamente durante os dez segundos após o boot. Anotar a linha `selftest ok=... ms=... frames=... error=...`.

Interpretação:
- `ok=1 ms=10000` — captura íntegra, siga
- `error=microphone unavailable` — o `prepareInput` falhou; investigar o barramento I2C e o trilho de áudio antes de qualquer outra coisa
- `error=microphone read returned nothing` — o RX do I2S não subiu; é aqui que a hipótese do full-duplex se prova ou cai
- `error=SD write failed` ou `ms` bem abaixo de 10000 — **é a contenção de SD que a spec previu como risco alto**

- [ ] **Step 4: Ouvir o arquivo**

Entrar no modo de transferência USB do aparelho (que já existe no firmware) ou plugar o cartão no PC, e copiar `/voice-selftest.wav`. Abrir e julgar: dá para entender a fala? Há estalos, cortes ou silêncio?

- [ ] **Step 5: Verificar o formato com as ferramentas que já temos**

```bash
cd bridge && uv run python -c "
from handy_bridge.wav import inspect
i = inspect(r'<caminho>/voice-selftest.wav')
print(i.sample_rate, 'Hz', i.channels, 'canal(is)', i.bits_per_sample, 'bits', round(i.duration_s,2), 's')
"
```
Expected: `16000 Hz 1 canal(is) 16 bits ~10.0 s`. Isto reusa o inspetor do plano 1 e prova que o WAV do device é exatamente o que o bridge espera.

- [ ] **Step 6: Fechar o ciclo completo, do microfone ao Obsidian**

Subir o bridge e enviar o arquivo gravado pelo próprio aparelho:

```bash
cd bridge && uv run python -m handy_bridge --config config.toml --log-level info
```

```bash
curl -X POST http://localhost:8787/v1/notes \
  -F "audio=@voice-selftest.wav;type=audio/wav;filename=selftest.wav" \
  -F 'meta={"clock_synced":false}'
```

Expected: uma nota aparece em `Reading/Inbox/` com a transcrição do que você falou. **Este é o momento em que a ideia inteira funciona pela primeira vez de ponta a ponta com hardware real.**

- [ ] **Step 7: Ajustar o ganho do microfone, se necessário**

Se a gravação sair baixa ou distorcida, alterar `kMicPgaGain` em `Es8311.cpp` (bits 3:0 do registrador `0x14`), recompilar e repetir os passos 2 a 4. Registrar o valor final num comentário ao lado da constante, com a justificativa medida.

- [ ] **Step 8: Registrar os resultados na spec**

Atualizar a seção de riscos com o que foi medido: full-duplex funcionou ou exigiu alternância, houve contenção de SD, qual ganho ficou, e se o `beep()` sobreviveu à gravação (o critério de sucesso 4 da spec).

- [ ] **Step 9: Commit**

```bash
git add docs/superpowers/specs/ src/drivers/audio/es8311/Es8311.cpp
git commit -m "docs(voice): record measured capture results from hardware"
```

---

## Definição de pronto

1. `pio test -e native_test` passa com os 7 testes novos do `WavWriter`
2. Os dois envs de firmware compilam, e uma plataforma não tocada (AMOLED 2.16) também
3. Um WAV gravado pelo microfone da placa existe no cartão e é audível
4. `handy_bridge.wav.inspect` confirma 16 kHz, mono, 16 bits
5. Esse mesmo arquivo, passado pelo bridge, produz uma nota no vault
6. O `beep()` continua funcionando depois de uma gravação
7. Os riscos de full-duplex, ganho de microfone e contenção de SD estão respondidos com medição, não com suposição

O plano 2b assume tudo isso resolvido e constrói a fila durável, o envio por mDNS e o SNTP. O gatilho real no leitor e a tela Voz ficam para o 2c, que é quando o andaime da Task 4 é removido.
