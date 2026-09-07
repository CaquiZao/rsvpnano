# Notas de voz no RSVP Nano → Obsidian

**Data:** 2026-09-07
**Status:** aprovado, aguardando plano de implementação
**Fork:** `CaquiZao/rsvpnano` (upstream: `ionutdecebal/rsvpnano`)
**Hardware alvo:** Waveshare ESP32-S3-Touch-LCD-3.49 (env `waveshare_esp32s3_touch_lcd_349_rev2`)

## 1. Problema

O RSVP Nano usa o display, os botões, o SD e o WiFi da placa, mas deixa o microfone e o alto-falante completamente ociosos. O codec ES8311 está inicializado apenas para saída — existe um `beep()` e nada mais. O pino de entrada do microfone (`kDinPin = 6`) está declarado em `WaveshareLcd349.h` e nunca é usado.

Ao mesmo tempo, capturar uma ideia falada e transformá-la em nota no Obsidian hoje exige tirar o celular do bolso, abrir um app, gravar, e depois transcrever à mão.

Este documento especifica o sub-projeto que fecha essa lacuna: **gravar uma nota de voz no device e recebê-la transcrita e organizada no vault do Obsidian, sem passo manual nenhum.**

## 2. Escopo

**Dentro:**

- Captura de áudio pelo microfone da placa (ES8311 ADC → I2S RX)
- Gravação em WAV 16 kHz mono 16-bit no cartão SD
- Fila durável no SD que sobrevive a ficar sem WiFi e a reboots
- Envio automático para um serviço no PC quando houver WiFi
- Transcrição local via Handy + Nemotron Streaming 3.5
- Geração de título, tags e versão limpa via CLI do `claude`
- Escrita da nota em Markdown no vault `Reading`
- Tela dedicada no device para gravar e acompanhar o estado da fila

**Fora (sub-projetos separados):**

- Identidade visual própria (tema) — trivial, entra depois
- Conversa por voz com IA — depende de tudo daqui funcionando primeiro
- Reprodução das notas gravadas no próprio device
- Companion apps (Android/iOS/Web) — não serão modificados

## 3. Decisões e porquês

| # | Decisão | Por quê |
|---|---|---|
| D1 | Transcrição roda no PC, não no ESP32 | Whisper/Nemotron quantizados pesam 0,5–2,5 GB; a placa tem 8 MB de PSRAM. O que roda on-device (ESP-SR) é detecção de comandos fixos, não ditado livre em português. Não há caminho viável no device. |
| D2 | Handy via CLI, não reimplementar ASR | `handy --transcribe-file` já expõe transcrição headless em lote, e o usuário já roda esse stack em português com o Nemotron. Reaproveitar em vez de reconstruir. |
| D3 | WAV 16 kHz mono | É o que o `--transcribe-file` do Handy exige **e** o padrão já configurado no `Es8311::Context`. Zero resampling em qualquer ponto da cadeia. |
| D4 | Fila no SD é a fonte da verdade | Grava-se offline (rua, ônibus) e envia-se depois. Nenhuma nota se perde se o PC estiver desligado. |
| D5 | Device empurra para o PC (push) | Escolha do usuário. Sensação de "gravou, chegou", sem depender de o PC estar rodando um poller. |
| D6 | Bridge descoberto por mDNS | O ESP32 consegue consultar mDNS. Elimina a configuração manual de IP que normalmente é o custo do modelo push. |
| D7 | Tela Voz dedicada, sem combo de botões | A placa tem só 2 botões e ambos já estão ocupados; long-press do PWR desliga o aparelho. Um combo arriscaria desligar o device no meio de uma ideia. |
| D8 | Título/tags via `claude` CLI | O usuário já tem o CLI instalado (plugin Claudian no vault). Zero dependência nova, e qualidade em português muito superior a um modelo pequeno local. |
| D9 | Transcrição crua sempre preservada | Contrapeso a D8: o LLM pode alucinar. O texto literal do Nemotron fica na mesma nota, num callout recolhido. |
| D10 | Tudo novo em módulos próprios | O usuário quer continuar recebendo melhorias do upstream. Só `Es8311.{h,cpp}` e `Screens.h` são tocados, e de forma aditiva. |

## 4. Arquitetura

```
┌─ ESP32-S3 ─────────────────────┐      ┌─ PC (Windows) ──────────────────────┐
│                                │      │                                     │
│  VoiceScreen                   │      │  handy-bridge (Python)              │
│    BOOT = gravar / parar       │      │    anuncia _handybridge._tcp        │
│    PWR  = sair                 │      │                                     │
│         ↓                      │      │    POST /v1/notes                   │
│  VoiceCapture                  │      │      grava WAV em disco → 200       │
│    ES8311 ADC, I2S RX, DIN=6   │      │         ↓ (assíncrono)              │
│         ↓                      │      │    handy --transcribe-file --json   │
│  WavWriter                     │      │         ↓                           │
│    16k mono 16-bit             │      │    claude -p --output-format json   │
│    ping-pong 2×32 KB (PSRAM)   │      │      → {title, tags, cleaned}       │
│         ↓                      │      │         ↓                           │
│  VoiceQueue                    │      │    escrita atômica                  │
│    /voice/pending/             │      │      Reading/Inbox/*.md             │
│         ↓                      │      │                                     │
│  VoiceUploader (task)          │ WAV  │                                     │
│    mDNS → POST ────────────────┼─────▶│                                     │
│    ACK 200 ────────────────────┼◀─────┤                                     │
│         ↓                      │      │                                     │
│    move → /voice/sent/         │      │                                     │
└────────────────────────────────┘      └─────────────────────────────────────┘
```

O ponto central do desenho: **gravar e enviar são desacoplados.** A gravação nunca depende de rede. O upload nunca bloqueia a UI.

## 5. Firmware — módulo `src/voice/`

### 5.1 `VoiceCapture`

Configura o caminho de entrada do ES8311 e lê blocos de PCM.

- Habilita o amplificador/codec via TCA9554 (`kAudioEnablePin = 7`, endereço `0x20`)
- Configura os registradores de ADC do ES8311 (endereço `0x18` no `Wire1`)
- Lê I2S RX no `I2S_NUM_0`, pinos MCLK=7, BCLK=15, WS=46, DIN=6

**Full duplex sai de graça.** `I2SClass::begin()` aloca `tx_chan` e `rx_chan` quando ambos os pinos de dados estão definidos (`ESP_I2S.cpp:505-509`). Hoje o `Context` só informa `dataOutPin`, por isso o periférico sobe em TX-only. A mudança é passar os dois em uma única chamada:

```cpp
i2s.setPins(bclkPin, wsPin, dataOutPin, dataInPin, mclkPin);
```

Gravação e `beep()` passam a conviver no mesmo periférico, sem alternância de modo.

Estende `src/drivers/audio/es8311/Es8311.{h,cpp}` **de forma aditiva**:

```cpp
bool prepareInput(Context& context);
bool readSamples(Context& context, int16_t* samples, size_t sampleCount, uint32_t timeoutMs);
```

`Context` ganha um campo `dataInPin` ao lado do `dataOutPin` existente.

### 5.2 `WavWriter`

Escreve WAV PCM 16 kHz / mono / 16-bit no SD.

- Buffer ping-pong de 2×32 KB em PSRAM. A 32 KB/s de vazão, cada metade dá 1 s de folga para a escrita no SD — margem ampla.
- Cabeçalho via `pcm_wav_header_t` e o macro `PCM_WAV_HEADER_DEFAULT(...)`, que já vêm no `wav_header.h` da própria `ESP_I2S` — nada de montar os 44 bytes à mão.
- Escrito no início com tamanhos zerados e corrigido no `close()`, evitando um segundo passe sobre o arquivo.
- Se o `close()` não acontecer (queda de energia), o arquivo fica em `pending/` com os tamanhos zerados. O bridge **conserta o cabeçalho** a partir do tamanho real do arquivo e transcreve normalmente — uma queda de energia não pode custar a nota. Só é rejeitado com 4xx o arquivo curto demais para conter áudio (menos de 1 s).

### 5.3 `VoiceQueue`

Estrutura no cartão:

```
/voice/pending/<timestamp>.wav
/voice/pending/<timestamp>.json
/voice/sent/
/voice/failed/
```

O `.json` carrega `duration_ms`, `recorded_at`, `clock_synced`, `uptime_ms`, `boot_id`, `device_id`, `board_id` e `firmware_version`. O par só sai de `pending/` depois do `200` do bridge.

#### Relógio

A placa não tem RTC com bateria, então o device pode não saber a hora real ao gravar offline depois de um reboot. O tratamento:

- Ao conectar no WiFi, o device sincroniza por NTP e marca `clock_synced = true`
- Gravando com relógio sincronizado: `recorded_at` é a hora real
- Gravando **sem** relógio sincronizado: `clock_synced = false`, e o `.json` carrega `boot_id` (aleatório por boot) e `uptime_ms`
- Nesse caso o bridge reconstrói a hora: `hora_de_chegada − (uptime_no_upload − uptime_ms)`. Como o upload acontece pouco depois de o WiFi subir, o erro é de minutos, não de horas.
- O nome do arquivo na fila usa `<boot_id>-<uptime_ms>` quando não há relógio, garantindo unicidade sem depender de data

O `date` no frontmatter sempre recebe a melhor estimativa disponível; quando ela veio de reconstrução, o frontmatter também ganha `date_estimated: true`.

### 5.4 `VoiceUploader`

Task FreeRTOS separada da UI.

- Quando o WiFi sobe: `MDNS.queryService("handybridge", "tcp")`
- Para cada item em `pending/`: `POST` multipart, aguarda resposta
- `200` → move para `sent/`
- `4xx` → move para `failed/` (falha permanente, não adianta insistir)
- `5xx` / timeout / sem bridge → mantém em `pending/`, backoff exponencial (30 s → 15 min)

Reusa o `HTTPClient` que `RssFeeds.cpp` e `OtaUpdater.cpp` já usam.

### 5.5 `VoiceScreen`

Tela nova, registrada em `src/ui/screens/Screens.h`.

- `BOOT` — inicia/para a gravação
- `PWR` — sai da tela
- Exibe: tempo decorrido, indicador de nível de entrada, contagem de pendentes/enviadas
- Estados visíveis: ocioso, gravando, salvando, enviando, enviado, falhou

Limite de 10 minutos por gravação, com aviso visual a partir dos 9.

### 5.6 `CompanionVoiceApi.cpp`

Expõe o estado da fila na API companion existente, seguindo o padrão de `CompanionFeedsApi.cpp` e `CompanionFocusTimerApi.cpp`. Opcional, barato, útil para depuração.

## 6. `handy-bridge` (PC)

Serviço Python único, rodando em background no Windows.

### Responsabilidades

1. Anunciar `_handybridge._tcp` na porta `8787` via `zeroconf`
2. Servir `POST /v1/notes`, persistir o WAV em disco e responder `200` imediatamente
3. Transcrever de forma assíncrona:
   `handy --transcribe-file <wav> --json --model handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf`
4. Pós-processar: `claude -p "<prompt>" --output-format json` retornando `{"title": str, "tags": [str], "cleaned": str}`
5. Escrever a nota em `Reading/Inbox/`

O `200` **antes** da transcrição é deliberado: o device não deve ficar com a rádio ligada esperando inferência. A responsabilidade do bridge no ACK é apenas "o áudio está seguro no meu disco".

### Configuração (`config.toml`)

```toml
vault_path   = "C:/Users/kakam/OneDrive/Área de Trabalho/Reading"
inbox_folder = "Inbox"
audio_store  = "C:/Users/kakam/.handy-bridge/audio"
port         = 8787

[asr]
model = "handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf"

[llm]
enabled = true
command = "claude"
```

### Degradação

Se o `claude` falhar ou estiver indisponível, a nota é escrita mesmo assim, com a transcrição crua no corpo e título por timestamp. **Uma falha no passo de LLM nunca pode custar uma nota.**

## 7. Formato da nota

Arquivo: `Inbox/2026-09-07 1432 — Ideia de fluxo de captura por voz.md`

```markdown
---
title: Ideia de fluxo de captura por voz
date: 2026-09-07T14:32:11
duration: 47s
source: rsvp-nano
asr_model: nemotron-3.5-asr-streaming-0.6b
tags: [ideia, projeto/rsvpnano]
---

Texto limpo pelo LLM, sem hesitações e com pontuação.

> [!note]- Transcrição original
> Texto cru do Nemotron, palavra por palavra.
```

A escrita é **atômica** — arquivo temporário seguido de rename — porque o vault vive dentro do OneDrive e escrita parcial pode virar cópia de conflito.

## 8. Protocolo device ↔ bridge

```
POST /v1/notes
Content-Type: multipart/form-data

  audio  →  <timestamp>.wav   (audio/wav)
  meta   →  <timestamp>.json  (application/json)

200 {"id": "<timestamp>", "status": "accepted"}
4xx {"error": "<motivo>"}      → falha permanente
5xx                            → device tenta de novo
```

## 9. Riscos

| Risco | Gravidade | Mitigação |
|---|---|---|
| ~~I2S full-duplex~~ — **resolvido na análise, não é mais risco** | — | `ESP_I2S.cpp:505-509` (arduino-esp32 3.3.9, versão fixada no `platformio.ini`) aloca `tx_chan` **e** `rx_chan` automaticamente quando `_dout >= 0 && _din >= 0`. O firmware está em TX-only só porque o `Context` nunca informa um pino de entrada. Basta passar `din = 6` em `setPins()`. Sem alternância de modo, sem risco para o `beep()`. |
| Qualidade do microfone em ambiente real | Média | Ajuste empírico do ganho do PGA; medir com gravações reais antes de fixar |
| Tempo de inferência do Nemotron desconhecido nesta máquina | Média | Medir com `--repeat` antes de prometer latência |
| OneDrive gerando cópias de conflito | Baixa | Escrita atômica (temp + rename) |
| Consumo de bateria com gravação + WiFi | Baixa | Upload só ocorre fora da gravação; rádio desligada quando a fila está vazia |
| Cartão SD cheio de `sent/` | Baixa | Retenção configurável; podar `sent/` acima de N dias |

## 10. Testes

- **Nativos** (env `native_test` já existente): `WavWriter` (cabeçalho, tamanhos, truncamento) e `VoiceQueue` (transições pending→sent→failed, backoff) — lógica pura, sem hardware
- **Bridge**: WAVs fixture, com Handy e `claude` mockados; testar a degradação sem LLM
- **Integração**: um WAV real gerado pelo device passando por todo o pipeline
- **Manual no hardware**: captura, nível de áudio, comportamento offline, reboot com fila cheia

## 11. Critérios de sucesso

1. Gravar uma nota de 30 s sem WiFi, chegar em casa, e encontrá-la no vault sem ter tocado em nada
2. A transcrição em português é fiel o suficiente para não precisar reouvir o áudio
3. Nenhuma nota é perdida com o PC desligado, WiFi ausente ou reboot no meio da fila
4. O beep do device continua funcionando depois de uma gravação
5. `git rebase upstream/main` continua sem conflitos relevantes

## 12. Futuro

**Sub-projeto 2 — identidade visual.** Um `themes/<nome>.toml` novo mais uma entrada em `themes/index.json`. Conflito zero com upstream.

**Sub-projeto 3 — conversa por voz.** Em vez de Gemini Live (que exigiria WebSocket, TLS e áudio bidirecional em tempo real no firmware, além da API key morando no device), a proposta é walkie-talkie: o device grava a pergunta, o bridge transcreve, o `claude` CLI responde **com o vault Reading como working directory**, e um TTS no PC devolve o áudio para o ES8311 tocar. Reusa toda a infraestrutura deste documento e entrega algo melhor — um agente que enxerga as próprias notas do usuário.
