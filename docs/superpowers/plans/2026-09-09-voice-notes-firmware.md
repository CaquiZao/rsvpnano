# Notas de voz no firmware — fila, envio, gatilho e interface

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gravar uma nota de voz de dentro do leitor com duplo clique no BOOT, ancorada na posição do livro, e entregá-la ao handy-bridge sem o usuário tocar em menu nenhum.

**Architecture:** Três camadas aditivas. `src/voice/` ganha metadados, fila em disco e envio HTTP; `src/app/` ganha um detector de duplo clique e uma task de envio em background; `src/ui/screens/` ganha a tela de gravação e a de notas. O gatilho vive no `App::handleInput`, que já é o único lugar onde ações de botão chegam — nenhuma mudança no `src/input/`, para o rebase com o upstream continuar trivial.

**Tech Stack:** Arduino-ESP32 3.3.9, C++23, ESP_I2S, SD_MMC, ESPmDNS, HTTPClient, glaze (JSON), Unity.

**Spec:** `docs/superpowers/specs/2026-09-07-notas-de-voz-obsidian-design.md`

## Global Constraints

- **Rebase fácil com o upstream é requisito, não preferência.** Arquivos novos em diretórios novos sempre que possível. Editar arquivo do upstream só quando não houver alternativa, e então no menor número de linhas.
- **Ganho do microfone: passo 8 (24 dB).** Medido: 37,5 dB clipa 5,5% das amostras; 24 dB cai em ~−21 dBFS.
- **O ES7210 é o chip de captura, em 0x40.** O ES8311 só toca. Ver §3.2 da spec.
- **Nunca rodar dois processos `pio` ao mesmo tempo** — corrompe o cache do SCons.
- **WAV é PCM 16 kHz mono 16-bit.** É o que o Handy exige.
- Toda escrita em disco que o bridge vai ler é atômica: arquivo temporário + rename.
- Português nas strings de interface; comentários e commits em inglês, como o resto do repo.

---

## Fase A — Metadados e fila

### Task A1: Metadados da nota ao lado do WAV

**Files:**
- Create: `src/voice/VoiceNoteMeta.h`, `src/voice/VoiceNoteMeta.cpp`
- Test: `test/test_voice_note_meta/test_voice_note_meta.cpp`
- Modify: `platformio.ini` (filtro do `native_test` e `test_filter`)

**Interfaces:**
- Consumes: nada.
- Produces:
  ```cpp
  namespace voice {
      struct NoteMeta {
          std::string recordedAt;   // ISO 8601 local, "" se o relógio não sincronizou
          bool clockSynced = false;
          std::string book;         // nome do arquivo do livro, sem extensão; "" fora do leitor
          uint32_t wordOffset = 0;
          std::string excerpt;      // parágrafo onde a gravação começou
          uint32_t durationMs = 0;
      };
      std::string toJson(const NoteMeta& meta);
      bool writeSidecar(const char* wavPath, const NoteMeta& meta);  // troca .wav por .json
      std::string sidecarPath(const char* wavPath);
  }
  ```

- [ ] **Step 1: Escrever o teste que falha**

```cpp
#include <unity.h>
#include "voice/VoiceNoteMeta.h"

void test_json_omits_the_book_when_there_is_none() {
    voice::NoteMeta meta;
    meta.recordedAt = "2026-09-09T14:03:00";
    meta.clockSynced = true;
    meta.durationMs = 21000;
    const std::string json = voice::toJson(meta);
    TEST_ASSERT_NOT_NULL(strstr(json.c_str(), "\"clock_synced\":true"));
    TEST_ASSERT_NULL(strstr(json.c_str(), "\"book\""));
    TEST_ASSERT_NULL(strstr(json.c_str(), "\"excerpt\""));
}

void test_json_carries_the_anchor_when_recorded_inside_the_reader() {
    voice::NoteMeta meta;
    meta.book = "epdf.pub_sapiens";
    meta.wordOffset = 12438;
    meta.excerpt = "a Revolucao Agricola foi a maior fraude";
    const std::string json = voice::toJson(meta);
    TEST_ASSERT_NOT_NULL(strstr(json.c_str(), "\"word_offset\":12438"));
    TEST_ASSERT_NOT_NULL(strstr(json.c_str(), "epdf.pub_sapiens"));
}

void test_a_quote_in_the_excerpt_does_not_break_the_json() {
    voice::NoteMeta meta;
    meta.excerpt = "ele disse \"nao\" e saiu";
    const std::string json = voice::toJson(meta);
    TEST_ASSERT_NOT_NULL(strstr(json.c_str(), "\\\"nao\\\""));
}

void test_sidecar_path_replaces_the_extension() {
    TEST_ASSERT_EQUAL_STRING("/voice/20260909-140300.json",
                             voice::sidecarPath("/voice/20260909-140300.wav").c_str());
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pio test -e native_test -f test_voice_note_meta`
Expected: FAIL, `VoiceNoteMeta.h` não existe.

- [ ] **Step 3: Implementar**

`toJson` monta o objeto à mão com um escape de string próprio — o glaze não está no filtro deste alvo e um objeto de seis campos não justifica a dependência. Campos vazios são **omitidos**, nunca enviados como `""`: o bridge distingue "nota solta" de "nota do leitor" pela ausência da chave.

- [ ] **Step 4: Rodar e ver passar**

Run: `pio test -e native_test -f test_voice_note_meta`
Expected: PASS, 4 testes.

- [ ] **Step 5: Commit**

```bash
git add src/voice/VoiceNoteMeta.* test/test_voice_note_meta platformio.ini
git commit -m "feat(voice): describe a recording in a JSON sidecar"
```

---

### Task A2: Fila em disco

**Files:**
- Create: `src/voice/VoiceQueue.h`, `src/voice/VoiceQueue.cpp`
- Test: `test/test_voice_queue/test_voice_queue.cpp`
- Modify: `platformio.ini`

**Interfaces:**
- Consumes: `voice::sidecarPath` (A1), `test/support/FS.h` para o teste nativo.
- Produces:
  ```cpp
  namespace voice {
      constexpr char kQueueDir[] = "/voice";
      struct QueueEntry {
          std::string wavPath;
          std::string metaPath;
          uint32_t sizeBytes = 0;
      };
      // Nome único e ordenável: prefere o relógio, cai para millis() se não sincronizou.
      std::string nextRecordingPath(const NoteMeta& meta, uint32_t bootMs);
      std::vector<QueueEntry> pending(fs::FS& fs);
      bool markSent(fs::FS& fs, const QueueEntry& entry);     // apaga wav e json
      bool markFailed(fs::FS& fs, const QueueEntry& entry, uint8_t attempts);
      size_t pendingCount(fs::FS& fs);
      bool ensureDir(fs::FS& fs);
  }
  ```

- [ ] **Step 1: Escrever o teste que falha**

```cpp
void test_a_fresh_queue_is_empty() {
    FakeFS fs;
    TEST_ASSERT_EQUAL(0, voice::pendingCount(fs));
}

void test_a_recording_with_its_sidecar_is_pending() {
    FakeFS fs;
    fs.write("/voice/a.wav", "RIFF");
    fs.write("/voice/a.json", "{}");
    const auto items = voice::pending(fs);
    TEST_ASSERT_EQUAL(1, items.size());
    TEST_ASSERT_EQUAL_STRING("/voice/a.wav", items[0].wavPath.c_str());
}

void test_a_wav_without_a_sidecar_is_still_pending() {
    // Perder a nota porque o JSON nao gravou seria pior que enviar sem ancora.
    FakeFS fs;
    fs.write("/voice/a.wav", "RIFF");
    TEST_ASSERT_EQUAL(1, voice::pending(fs).size());
}

void test_a_sidecar_without_a_wav_is_swept_not_reported() {
    FakeFS fs;
    fs.write("/voice/a.json", "{}");
    TEST_ASSERT_EQUAL(0, voice::pending(fs).size());
    TEST_ASSERT_FALSE(fs.exists("/voice/a.json"));
}

void test_marking_sent_removes_both_files() {
    FakeFS fs;
    fs.write("/voice/a.wav", "RIFF");
    fs.write("/voice/a.json", "{}");
    voice::markSent(fs, voice::pending(fs)[0]);
    TEST_ASSERT_FALSE(fs.exists("/voice/a.wav"));
    TEST_ASSERT_FALSE(fs.exists("/voice/a.json"));
}

void test_the_queue_is_ordered_oldest_first() {
    FakeFS fs;
    for (const char* n : {"/voice/20260909-1200.wav", "/voice/20260908-0900.wav"})
        fs.write(n, "RIFF");
    TEST_ASSERT_EQUAL_STRING("/voice/20260908-0900.wav", voice::pending(fs)[0].wavPath.c_str());
}

void test_a_recording_that_failed_too_many_times_is_parked_not_retried_forever() {
    FakeFS fs;
    fs.write("/voice/a.wav", "RIFF");
    auto entry = voice::pending(fs)[0];
    TEST_ASSERT_TRUE(voice::markFailed(fs, entry, 5));
    TEST_ASSERT_EQUAL(0, voice::pending(fs).size());
    TEST_ASSERT_TRUE(fs.exists("/voice/a.wav.parked"));
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pio test -e native_test -f test_voice_queue`

- [ ] **Step 3: Implementar**

O parking depois de 5 tentativas existe para a fila não travar atrás de um arquivo corrompido, mas o áudio **nunca é apagado** — renomear preserva a gravação para inspeção manual. Um WAV sem sidecar segue na fila; um sidecar órfão é lixo de uma gravação abortada e é varrido.

- [ ] **Step 4: Rodar e ver passar**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(voice): keep pending recordings in a queue on the card"
```

---

### Task A3: Relógio por SNTP

**Files:**
- Create: `src/voice/Clock.h`, `src/voice/Clock.cpp`
- Modify: `src/app/App.cpp` (uma chamada em `begin()`)

**Interfaces:**
- Produces:
  ```cpp
  namespace voice {
      // Nao bloqueia: dispara o SNTP e volta. Requer WiFi ja associado.
      void beginTimeSync(const char* timezone = "<-03>3");
      bool clockSynced();
      std::string nowIso8601();       // "" se nao sincronizou
      std::string compactStamp();     // "20260909-140300", ou "boot-000123" sem relogio
  }
  ```

- [ ] **Step 1: Implementar sobre `esp_sntp`**

`configTzTime` com o fuso do usuário. `clockSynced()` compara o ano com 2020 — o critério é grosseiro de propósito: um relógio em 1970 é o único erro que importa distinguir.

- [ ] **Step 2: Chamar no `App::begin()` depois do WiFi**

Sem WiFi o SNTP falha em silêncio e `compactStamp()` cai para `boot-<millis>`. A nota chega ao vault com data do arquivo em vez de data da fala; o bridge já trata `clock_synced: false`.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(voice): sync the clock so notes carry the time they were spoken"
```

---

### Task A4: Envio ao bridge

**Files:**
- Create: `src/voice/VoiceUploader.h`, `src/voice/VoiceUploader.cpp`
- Test: `test/test_voice_uploader/test_voice_uploader.cpp` (só a montagem do multipart, sem rede)

**Interfaces:**
- Consumes: `voice::QueueEntry` (A2).
- Produces:
  ```cpp
  namespace voice {
      struct Endpoint { std::string host; uint16_t port = 0; };
      // Procura _handybridge._tcp. Requer WiFi associado.
      std::optional<Endpoint> discoverBridge(uint32_t timeoutMs = 3000);
      enum class UploadResult : uint8_t { Sent, Retry, Rejected };
      UploadResult upload(fs::FS& fs, const Endpoint& endpoint, const QueueEntry& entry);
      // Cabecalho e rodape do corpo multipart, separados para poder testar sem rede.
      std::string multipartHeader(const std::string& boundary, const std::string& filename,
                                  const std::string& metaJson);
      std::string multipartFooter(const std::string& boundary);
  }
  ```

- [ ] **Step 1: Escrever o teste que falha**

```cpp
void test_the_multipart_names_the_fields_the_bridge_expects() {
    const auto head = voice::multipartHeader("BOUNDARY", "20260909-140300.wav", "{\"a\":1}");
    TEST_ASSERT_NOT_NULL(strstr(head.c_str(), "name=\"meta\""));
    TEST_ASSERT_NOT_NULL(strstr(head.c_str(), "name=\"audio\""));
    TEST_ASSERT_NOT_NULL(strstr(head.c_str(), "filename=\"20260909-140300.wav\""));
    TEST_ASSERT_NOT_NULL(strstr(head.c_str(), "Content-Type: audio/wav"));
}

void test_the_footer_closes_the_boundary() {
    TEST_ASSERT_EQUAL_STRING("\r\n--BOUNDARY--\r\n", voice::multipartFooter("BOUNDARY").c_str());
}
```

- [ ] **Step 2: Rodar e ver falhar**

- [ ] **Step 3: Implementar**

O corpo é enviado **em streaming** a partir do arquivo, nunca montado inteiro em RAM: uma nota de dez minutos são 19 MB e a PSRAM tem outros usos. `UploadResult::Rejected` (4xx) apaga a nota da fila — reenviar o que o bridge recusou é loop infinito. `Retry` (rede, 5xx) mantém.

- [ ] **Step 4: Rodar e ver passar**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(voice): push pending recordings to the bridge over mDNS"
```

---

### Task A5: Task de envio em background

**Files:**
- Create: `src/voice/VoiceService.h`, `src/voice/VoiceService.cpp`
- Modify: `src/app/App.h` (um membro), `src/app/App.cpp` (duas chamadas)

**Interfaces:**
- Consumes: A2, A3, A4.
- Produces:
  ```cpp
  namespace voice {
      class Service {
      public:
          bool begin();                       // cria a task, prioridade baixa
          void requestFlush();                // acorda a task
          size_t pendingCount() const;        // para o indicador na interface
          bool busy() const;
      };
  }
  ```

- [ ] **Step 1: Implementar a task**

Uma task dedicada em prioridade 1, stack 6 KB. Acorda por semáforo depois de cada gravação e a cada 5 minutos. Só liga o WiFi se houver fila — a rádio é o maior consumidor da placa e uma fila vazia não justifica acordá-la.

- [ ] **Step 2: Ligar no `App`**

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(voice): flush the queue from a background task"
```

---

## Fase B — Gatilho e interface

### Task B1: Duplo clique no BOOT

**Files:**
- Create: `src/voice/DoubleClick.h`, `src/voice/DoubleClick.cpp`
- Test: `test/test_double_click/test_double_click.cpp`
- Modify: `src/app/App.cpp` (`handleInput`, poucas linhas)

**Interfaces:**
- Produces:
  ```cpp
  namespace voice {
      class DoubleClick {
      public:
          enum class Verdict : uint8_t { Nothing, Single, Double };
          // Chamar a cada ActionPlayPause. `Single` sai atrasado, so quando a janela
          // fecha sem o segundo clique -- e o preco de o primeiro clique nao piscar.
          Verdict onPress(uint32_t nowMs);
          Verdict tick(uint32_t nowMs);
          static constexpr uint32_t kWindowMs = 260;
      };
  }
  ```

- [ ] **Step 1: Escrever o teste que falha**

```cpp
void test_one_press_alone_becomes_a_single_only_after_the_window() {
    voice::DoubleClick d;
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Nothing, (int)d.onPress(1000));
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Nothing, (int)d.tick(1100));
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Single, (int)d.tick(1300));
}

void test_two_presses_inside_the_window_are_a_double() {
    voice::DoubleClick d;
    d.onPress(1000);
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Double, (int)d.onPress(1150));
}

void test_two_presses_outside_the_window_are_two_singles() {
    voice::DoubleClick d;
    d.onPress(1000);
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Single, (int)d.tick(1300));
    d.onPress(1400);
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Single, (int)d.tick(1700));
}

void test_a_double_does_not_leak_a_single_afterwards() {
    voice::DoubleClick d;
    d.onPress(1000);
    d.onPress(1100);
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Nothing, (int)d.tick(1500));
}

void test_a_third_press_starts_a_new_window() {
    voice::DoubleClick d;
    d.onPress(1000);
    d.onPress(1100);
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Nothing, (int)d.onPress(1200));
}

void test_millis_wrapping_does_not_swallow_a_click() {
    voice::DoubleClick d;
    d.onPress(0xFFFFFF00u);
    TEST_ASSERT_EQUAL((int)voice::DoubleClick::Verdict::Double, (int)d.onPress(0x00000050u));
}
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `pio test -e native_test -f test_double_click`

- [ ] **Step 3: Implementar**

Subtração sem sinal em `uint32_t` resolve o wrap do `millis()` sem caso especial.

- [ ] **Step 4: Rodar e ver passar**

- [ ] **Step 5: Ligar no `App::handleInput`**

O único ponto no upstream que muda de comportamento. O `toggle()` de play/pause passa a esperar o veredito em vez de rodar na hora — 260 ms de atraso no play/pause em troca de gravar sem sair da leitura. Fora da tela do leitor o detector não roda: nas telas de menu o clique único continua imediato.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(voice): start a recording with a double click on BOOT"
```

---

### Task B2: Âncora no livro

**Files:**
- Create: `src/voice/BookAnchor.h`, `src/voice/BookAnchor.cpp`
- Test: `test/test_book_anchor/test_book_anchor.cpp`

**Interfaces:**
- Consumes: `ReadingSession`, `ReadingLoop::paragraphAt`, `voice::NoteMeta` (A1).
- Produces:
  ```cpp
  namespace voice {
      constexpr size_t kExcerptMaxChars = 400;
      // Le a posicao atual e o paragrafo em volta. Sem livro aberto, devolve
      // um NoteMeta sem book/excerpt -- o bridge trata como nota solta.
      NoteMeta anchorFor(const ReadingSession& session);
      // Corta em fronteira de palavra e nunca no meio de um UTF-8.
      std::string clampExcerpt(std::string_view paragraph, size_t maxChars = kExcerptMaxChars);
      std::string bookSlug(std::string_view sourcePath);
  }
  ```

- [ ] **Step 1: Escrever o teste que falha**

```cpp
void test_the_slug_drops_the_directory_and_the_extension() {
    TEST_ASSERT_EQUAL_STRING("epdf.pub_sapiens",
                             voice::bookSlug("/books/epdf.pub_sapiens.rsvp").c_str());
}

void test_a_short_paragraph_is_kept_whole() {
    TEST_ASSERT_EQUAL_STRING("um trecho curto", voice::clampExcerpt("um trecho curto", 400).c_str());
}

void test_a_long_paragraph_is_cut_on_a_word_boundary() {
    const std::string cut = voice::clampExcerpt("alfa beta gama delta", 12);
    TEST_ASSERT_EQUAL_STRING("alfa beta", cut.c_str());
}

void test_the_cut_never_splits_a_utf8_character() {
    // "coracao" com cedilha e til: o corte em 8 bytes cairia no meio do "c-cedilha".
    const std::string cut = voice::clampExcerpt("coracao ...", 8);
    TEST_ASSERT_TRUE(cut.empty() || (static_cast<unsigned char>(cut.back()) & 0xC0) != 0x80);
}
```

- [ ] **Step 2: Rodar e ver falhar**

- [ ] **Step 3: Implementar**

- [ ] **Step 4: Rodar e ver passar**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(voice): anchor a recording to where the reader was"
```

---

### Task B3: Tela de gravação

**Files:**
- Create: `src/ui/screens/VoiceRecordScreen.h`, `src/ui/screens/VoiceRecordScreen.cpp`
- Test: `test/test_voice_screens/test_voice_record_screen.cpp`
- Modify: `src/ui/screens/Screens.h` (um enum), `src/ui/Localization.h` + `Localization.generated.cpp` (strings)

**Interfaces:**
- Consumes: `ui::Context`, tema ativo.
- Produces:
  ```cpp
  namespace screens {
      struct VoiceRecordModel {
          uint32_t elapsedMs = 0;
          uint8_t level = 0;            // 0..255, RMS do ultimo bloco
          bool armed = false;           // gravando de fato
          const char* error = nullptr;
      };
      class VoiceRecordScreen {
      public:
          Action draw(ui::Context& ui, const VoiceRecordModel& model, uint32_t nowMs);
      private:
          std::array<uint8_t, 64> history_{};   // janela rolante de niveis
          size_t head_ = 0;
      };
      // Testavel sem display: mapeia RMS para altura de barra.
      uint8_t waveformBarHeight(uint8_t level, uint8_t maxHeight);
  }
  ```

- [ ] **Step 1: Escrever o teste que falha**

```cpp
void test_silence_draws_a_visible_baseline_not_nothing() {
    // Uma barra de altura zero parece travamento. O piso e 1 px de proposito.
    TEST_ASSERT_EQUAL(1, screens::waveformBarHeight(0, 40));
}

void test_full_level_fills_the_bar() {
    TEST_ASSERT_EQUAL(40, screens::waveformBarHeight(255, 40));
}

void test_the_mapping_is_monotonic() {
    uint8_t previous = 0;
    for (int level = 0; level <= 255; ++level) {
        const uint8_t h = screens::waveformBarHeight(static_cast<uint8_t>(level), 40);
        TEST_ASSERT_TRUE(h >= previous);
        previous = h;
    }
}
```

- [ ] **Step 2: Rodar e ver falhar**

- [ ] **Step 3: Implementar**

Tela cheia, cores tiradas do tema ativo — nada hardcoded, senão os 21 temas ficam com uma tela fora do lugar. Mostra tempo decorrido em `m:ss`, a forma de onda rolando e uma linha de instrução (`Duplo clique para parar`). Ocupa a orientação do leitor.

- [ ] **Step 4: Rodar e ver passar**

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(voice): show a full-screen waveform while recording"
```

---

### Task B4: Gravação de duração ilimitada

**Files:**
- Modify: `src/voice/VoiceCapture.h`, `src/voice/VoiceCapture.cpp`

**Interfaces:**
- Produces:
  ```cpp
  namespace voice {
      struct CaptureCallbacks {
          // Retorne false para parar. Chamado a cada bloco (~0,5 s).
          std::function<bool(uint32_t elapsedMs, uint8_t level)> onBlock;
      };
      CaptureResult captureToFile(const char* path, uint32_t maxDurationMs, uint8_t micGain = 8,
                                  uint8_t micPair = 0, const CaptureCallbacks* callbacks = nullptr);
  }
  ```

- [ ] **Step 1: Adicionar o callback e o nível por bloco**

`maxDurationMs == 0` passa a significar **sem limite**: para quando o callback pedir, quando o cartão enche, ou no teto de segurança de 30 minutos. Sem teto nenhum um bolso poderia gravar até o cartão encher.

- [ ] **Step 2: Calcular o RMS do bloco**

Inteiro, sem `sqrt` por amostra — soma de quadrados em `uint64_t` e uma raiz por bloco.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(voice): record without a time limit and report the level"
```

---

### Task B5: Ligar gatilho, captura e tela

**Files:**
- Modify: `src/app/App.h`, `src/app/App.cpp`
- Create: `src/voice/VoiceRecorder.h`, `src/voice/VoiceRecorder.cpp`

**Interfaces:**
- Consumes: A1, A2, A3, A5, B1, B2, B3, B4.
- Produces:
  ```cpp
  namespace voice {
      class Recorder {
      public:
          bool start(const NoteMeta& anchor);   // cria a task de gravacao
          void stop();
          bool active() const;
          uint32_t elapsedMs() const;
          uint8_t level() const;
          const char* error() const;
      };
  }
  ```

- [ ] **Step 1: Implementar o `Recorder`**

Task dedicada, prioridade 2, stack 8 KB. Grava numa task própria porque `captureToFile` bloqueia e a interface tem de continuar desenhando a forma de onda. O `stop()` levanta uma flag que o callback lê — nada de matar task no meio de uma escrita no cartão.

- [ ] **Step 2: Ligar no `App`**

Duplo clique na tela do leitor → pausa a leitura, captura a âncora, apita, abre a `VoiceRecordScreen` e começa. Duplo clique de novo → para, apita diferente, escreve o sidecar, chama `Service::requestFlush()` e volta ao leitor exatamente onde estava.

- [ ] **Step 3: Verificar no aparelho**

Gravar 30 s falando, conferir que a nota aparece no vault com `book`, `word_offset` e o trecho.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(voice): record a note from inside the reader"
```

---

### Task B6: Realimentação sonora

**Files:**
- Create: `src/voice/Tones.h`, `src/voice/Tones.cpp`
- Modify: `src/board/BoardAudio.h`, `src/platforms/waveshare_lcd_349/BoardAudio.cpp`

**Interfaces:**
- Produces:
  ```cpp
  namespace voice {
      enum class Tone : uint8_t { Start, Stop, Sent, Error };
      void play(Tone tone);
  }
  ```

- [ ] **Step 1: Implementar**

Quatro timbres distinguíveis sem olhar a tela: `Start` sobe, `Stop` desce, `Sent` são dois tons curtos, `Error` é um grave repetido. É a única confirmação quando a placa está no bolso.

- [ ] **Step 2: Commit**

```bash
git commit -m "feat(voice): tell the recording state apart by ear"
```

---

### Task B7: Tela de notas e indicador de pendências

**Files:**
- Create: `src/ui/screens/VoiceNotesScreen.h`, `src/ui/screens/VoiceNotesScreen.cpp`
- Modify: `src/ui/screens/DeviceScreen.cpp` (uma entrada de menu), `src/ui/screens/ReaderScreen.cpp` (o indicador)

**Interfaces:**
- Consumes: `voice::Service::pendingCount()`, `voice::pending()`.

- [ ] **Step 1: Implementar a tela**

Lista as notas na fila com data e duração, e um botão de reenviar. Fila vazia mostra "Nenhuma nota pendente" — uma tela vazia sem texto parece defeito.

- [ ] **Step 2: Indicador no leitor**

Um ponto discreto no canto quando `pendingCount() > 0`. Desaparece sozinho quando a fila esvazia. Cor do tema.

- [ ] **Step 3: Commit**

```bash
git commit -m "feat(voice): list pending notes and mark them in the reader"
```

---

### Task B8: Remover o andaime do autoteste

**Files:**
- Modify: `src/app/App.cpp` (bloco `RSVP_VOICE_SELFTEST`), `platformio.ini` (env `voice_selftest_*`)

- [ ] **Step 1: Apagar o bloco e o env**

O autoteste cumpriu o papel: descobriu o ES7210, o endereço, a sequência que faltava e o ganho. Manter um bloco que grava no boot é armadilha para quem clonar o repo. O que ele ensinou está na spec, que é onde conhecimento dura.

- [ ] **Step 2: Confirmar que os outros ambientes compilam**

Run: `pio run -e waveshare_esp32s3_touch_lcd_349_rev2`

- [ ] **Step 3: Commit**

```bash
git commit -m "chore(voice): retire the boot-time capture scaffold"
```

---

## Fase C — Progresso por capítulo

### Task C1: Duas barras e marcas de anotação

**Files:**
- Create: `src/reader/ChapterProgress.h`, `src/reader/ChapterProgress.cpp`
- Test: `test/test_chapter_progress/test_chapter_progress.cpp`
- Modify: `src/ui/screens/ReaderScreen.cpp` (o desenho da barra)

**Interfaces:**
- Consumes: `ChapterMarker`, `ReadingSession`.
- Produces:
  ```cpp
  namespace reading {
      struct ChapterPosition {
          size_t index = 0;           // capitulo atual
          size_t count = 0;
          uint8_t percentInChapter = 0;
          uint8_t percentInBook = 0;
          std::string_view title;
      };
      ChapterPosition chapterPositionAt(std::span<const ChapterMarker> chapters,
                                        size_t wordIndex, size_t wordCount);
  }
  ```

- [ ] **Step 1: Escrever o teste que falha**

```cpp
void test_a_book_without_chapters_reports_only_the_book_percent() {
    const auto p = reading::chapterPositionAt({}, 50, 100);
    TEST_ASSERT_EQUAL(0, p.count);
    TEST_ASSERT_EQUAL(50, p.percentInBook);
}

void test_the_middle_of_the_second_chapter_reads_fifty_percent() {
    const std::array<ChapterMarker, 3> chapters = {
        ChapterMarker{"um", 0}, ChapterMarker{"dois", 100}, ChapterMarker{"tres", 200}};
    const auto p = reading::chapterPositionAt(chapters, 150, 300);
    TEST_ASSERT_EQUAL(1, p.index);
    TEST_ASSERT_EQUAL(50, p.percentInChapter);
    TEST_ASSERT_EQUAL_STRING("dois", std::string(p.title).c_str());
}

void test_the_last_chapter_runs_to_the_end_of_the_book() {
    const std::array<ChapterMarker, 2> chapters = {ChapterMarker{"um", 0}, ChapterMarker{"dois", 100}};
    const auto p = reading::chapterPositionAt(chapters, 150, 200);
    TEST_ASSERT_EQUAL(50, p.percentInChapter);
}

void test_a_position_before_the_first_marker_belongs_to_the_first_chapter() {
    const std::array<ChapterMarker, 1> chapters = {ChapterMarker{"um", 10}};
    TEST_ASSERT_EQUAL(0, reading::chapterPositionAt(chapters, 5, 100).index);
}

void test_an_empty_chapter_does_not_divide_by_zero() {
    const std::array<ChapterMarker, 2> chapters = {ChapterMarker{"um", 50}, ChapterMarker{"dois", 50}};
    TEST_ASSERT_EQUAL(0, reading::chapterPositionAt(chapters, 50, 100).percentInChapter);
}
```

- [ ] **Step 2: Rodar e ver falhar**

- [ ] **Step 3: Implementar**

- [ ] **Step 4: Rodar e ver passar**

- [ ] **Step 5: Desenhar as duas barras**

Barra de cima: o capítulo, que é o que se mexe numa sessão de leitura. Barra de baixo, mais fina: o livro. O problema relatado era exato — ler um capítulo inteiro movia menos de 4% da barra única, e a leitura parecia não render.

As marcas de anotação vão na barra do capítulo, nas posições das notas daquele livro.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(reader): show chapter progress next to book progress"
```

---

## Auto-revisão

**Cobertura da spec.** D1–D15 cobertos: gatilho por duplo clique (B1), duração ilimitada (B4), âncora e trecho (B2), fila e reenvio (A2, A4, A5), relógio (A3), interface de gravação (B3), tela de notas e indicador (B7), progresso por capítulo (C1). Kanban, Telegram, digest e pós-processamento já estão prontos no bridge.

**Fora de escopo deste plano, por decisão:** vocabulário com repetição espaçada, painel de estatísticas, jogos, Gemini Live. Cada um é um sub-projeto com sua própria spec.

**Consistência de tipos.** `NoteMeta` é definido em A1 e consumido em A2, A4, B2 e B5 com o mesmo nome de campos. `QueueEntry` em A2, consumido em A4 e A5. `CaptureCallbacks` em B4, consumido em B5.
