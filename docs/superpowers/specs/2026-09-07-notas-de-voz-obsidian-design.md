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
- Escrita da nota em Markdown no vault `Reading`, com âncora no livro
- **Gatilho dentro do leitor** (duplo clique no BOOT), sem passar por menu
- **Duração sem limite fixo**, com parada automática por bateria ou espaço
- Relógio SNTP, para que a nota tenha hora real quando houver Wi-Fi
- Feedback sonoro pelo alto-falante nas transições de estado
- Harness de preview nativo, para iterar a UI 640×172 sem gravar firmware
- Conversão do epub para markdown no vault, uma vez por livro
- Tela Voz como **fila e histórico** — não é a porta de entrada da gravação

**Fora (sub-projetos separados):**

- Vocabulário com repetição espaçada — sub-projeto 3, reusa fila e bridge
- Painel de estatísticas de leitura — sub-projeto 4, independente
- Identidade visual própria (tema) — trivial, entra depois
- Conversa por voz com IA — depende de tudo daqui funcionando primeiro
- Bichinho virtual estilo Tamagotchi e jogos — escopo futuro
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
| D7 | **Duplo clique no BOOT, de dentro do leitor** | Decisão revisada em 2026-09-08. A escolha original era uma tela Voz dedicada, porque os 2 botões já estão ocupados e long-press do PWR desliga o aparelho. Na prática isso criava atrito justamente onde a ideia nasce: para anotar era preciso sair do livro e navegar até um menu. O duplo clique é um botão físico, usável sem olhar, e não rouba função de nada. **Custo aceito:** o `PlayPause` do clique único passa a ser suprimido por ~250 ms até a janela de duplo clique fechar, o que adiciona latência perceptível em todo play/pause do leitor. |
| D11 | Duração sem limite fixo | Anotação falada boa não caiba em 10 s. Em vez de um teto arbitrário, para automaticamente por bateria baixa ou espaço em disco. 1 h de áudio = 115 MB, o que um SD absorve sem drama. |
| D12 | Âncora = wikilink + offset + **trecho do texto** | Só o offset é inútil: obrigaria a reproduzir a tokenização do firmware sobre um epub de 4,7 MB. O firmware já tem o texto na RAM no instante do clique, então embutir a frase custa quase nada e torna a nota autossuficiente — para você e para o Claudian. |
| D13 | Bridge converte o epub para markdown | Uma vez por livro. Dá ao Claudian o livro inteiro como texto pesquisável, para perguntas que o trecho não alcança. |
| D14 | Relógio SNTP | O upstream não tem relógio nenhum (zero ocorrências de `configTime`/`sntp`). Com SNTP a hora é real quando há Wi-Fi, e a reconstrução por `uptime_ms` fica só como fallback para gravação offline pós-reboot. |
| D15 | Harness de preview nativo | Renderiza a UI 640×172 em PNG no PC. Pré-requisito prático para iterar tela Voz, tema e fonte sem gravar firmware a cada ajuste. Ideia tomada do fork RSVPbookworm — a ideia, não o código. |
| D8 | Título/tags via `claude` CLI | O usuário já tem o CLI instalado (plugin Claudian no vault). Zero dependência nova, e qualidade em português muito superior a um modelo pequeno local. |
| D9 | Transcrição crua sempre preservada | Contrapeso a D8: o LLM pode alucinar. O texto literal do Nemotron fica na mesma nota, num callout recolhido. |
| D10 | Tudo novo em módulos próprios | O usuário quer continuar recebendo melhorias do upstream. Só `Es8311.{h,cpp}` e `Screens.h` são tocados, e de forma aditiva. |

## 3.1 Ambiente de build (armadilhas verificadas)

Descobertas ao preparar o ambiente em 2026-09-08. Cada uma custou tempo e nenhuma é
óbvia:

- **A placa é rev2.** Não pelo rótulo, mas por dedução: rev1 e rev2 trocam entre si os
  pinos de backlight e de interrupção do touch (8 ↔ 42), então firmware rev1 numa placa
  rev2 resultaria em tela apagada ou touch morto. O firmware rev2 grava e funciona,
  logo é rev2. Env de build: `waveshare_esp32s3_touch_lcd_349_rev2`.
- **Rodar `pio` sob Git Bash / MSYS não funciona.** O `idf_tools.py` da Espressif aborta
  com `ERROR: MSys/Mingw is not supported`, e o efeito é traiçoeiro: o pacote
  `toolchain-xtensa-esp-elf` fica instalado como um **stub de 6 KB**, o build parece
  progredir e falha depois com `'xtensa-esp32s3-elf-g++' não é reconhecido`. A mensagem
  final não tem relação com a causa. **Rode sempre pelo PowerShell ou cmd.**
- **Os submódulos são obrigatórios.** `git clone` sem `--recurse-submodules` deixa
  `lib/HarfBuzz`, `lib/PDFio`, `lib/SheenBidi` e `lib/zlib` vazios e o build falha.
  Corrigir com `git submodule update --init --depth 1`.
- **O toolchain instalado traz os dois esquemas de nome.** `main.py:102` monta
  `toolchain_arch = "xtensa-%s" % mcu`, gerando `xtensa-esp32s3-elf-g++`, enquanto o
  pacote se chama `toolchain-xtensa-esp-elf`. Não é incompatibilidade: o crosstool-NG da
  Espressif instala `xtensa-esp-elf-g++` **e** os nomes por chip. Se o binário por chip
  não existir, o problema é download incompleto, não versão errada.
- **O env `native_test` exige um compilador de host** (`platform = native`). Sem g++,
  clang ou MSVC no PATH, nenhum teste de lógica pura do firmware roda.
- **`native_test` não builda no Windows sem trabalho extra.** `platformio.ini:471` fixa
  `-lz`, e o mingw não traz a zlib do sistema — a CI do autor roda em Linux, onde ela
  existe. A solução abaixo **não altera nenhum arquivo do repositório**, preservando o
  rebase fácil (D10): compila a zlib do submódulo que o projeto já versiona e injeta o
  caminho por variável de ambiente.

  ```bash
  # 1. Compilar a zlib vendorizada com o mingw (uma vez)
  MINGW=<...>/mingw64/bin
  ZSRC=lib/zlib/upstream
  for f in adler32 compress crc32 deflate gzclose gzlib gzread gzwrite \
           infback inffast inflate inftrees trees uncompr zutil; do
    "$MINGW/gcc.exe" -O2 -c "$ZSRC/$f.c" -I"$ZSRC" -o "$f.o"
  done
  "$MINGW/ar.exe" rcs libz.a *.o
  ```

  ```powershell
  # 2. Rodar os testes apontando para ela (PowerShell, nunca Git Bash)
  $env:Path = "<mingw64\bin>;$env:Path"
  $env:PLATFORMIO_BUILD_FLAGS = "-L<pasta com libz.a>"
  pio test -e native_test
  ```

  Isso é um candidato natural a PR para o upstream, caso a estratégia de fork mude.

### Baseline verificado em 2026-09-08

| Verificação | Resultado |
|---|---|
| `pio run -e waveshare_esp32s3_touch_lcd_349_rev2` | SUCCESS em 15m40s, sem alterações |
| `pio test -e native_test` | **196 casos, 196 passaram** (14 suítes) |
| `cd bridge && uv run pytest` | 78 passaram |

Qualquer regressão a partir daqui é do nosso código, não do ambiente.

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

Tela nova, registrada em `src/ui/screens/Screens.h`. **Não é a porta de entrada da
gravação** — serve para revisar o que foi capturado.

- Lista as notas: pendentes, enviadas, falhadas
- Permite forçar o envio agora
- Mostra o estado da última gravação e o espaço restante no cartão

O gatilho da gravação vive no leitor (D7), não aqui.

#### Estados durante a gravação, sinalizados no leitor

A gravação acontece **sobre a tela de leitura**, com um indicador discreto para não
competir com o texto. Cada transição toca um som (D-sonoro):

| Estado | Sinal visual | Som |
|---|---|---|
| Gravando | Ponto vermelho + tempo decorrido | Tom ascendente curto |
| Parada e salva | Indicador some | Tom descendente curto |
| Enviada | — | Confirmação de dois tons |
| Falhou | Ícone de alerta na tela Voz | Tom grave |

Sem limite fixo de duração (D11): para automaticamente com bateria abaixo de 15% ou
menos de 200 MB livres no cartão, tocando o som de falha.

### 5.6 `CompanionVoiceApi.cpp`

Expõe o estado da fila na API companion existente, seguindo o padrão de `CompanionFeedsApi.cpp` e `CompanionFocusTimerApi.cpp`. Opcional, barato, útil para depuração.

## 6. `handy-bridge` (PC)

Serviço Python único, rodando em background no Windows.

### Responsabilidades

1. Anunciar `_handybridge._tcp` na porta `8787` via `zeroconf`
2. Servir `POST /v1/notes`, persistir o WAV em disco e responder `200` imediatamente
3. Transcrever de forma assíncrona (valores verificados nesta máquina):

   ```
   C:\Users\kakam\AppData\Local\Handy\handy.exe --transcribe-file <wav> --json \
     --model handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf/nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf
   ```

   O ID do modelo inclui o nome do arquivo `.gguf`, não apenas o repositório. O
   modelo está baixado (716 MB, suporta português). Os **logs saem em stderr e o
   JSON em stdout**, então basta parsear stdout. Formato de saída:

   ```json
   {"audio_secs":2.0,"best_ms":1508,"load_ms":1366,"model":"...","rtf":1.326,"text":"...","transcribe_ms":[1508]}
   ```

   O campo relevante é `text`. Sai com código 0.

4. Pós-processar por uma interface plugável (`PostProcessor`) com três
   implementações: `claude` CLI (padrão), API Anthropic direta e Ollama. Todas
   retornam `{"title": str, "tags": [str], "cleaned": str}`.
5. Escrever a nota em `Reading/Inbox/`

O `200` **antes** da transcrição é deliberado: o device não deve ficar com a rádio ligada esperando inferência. A responsabilidade do bridge no ACK é apenas "o áudio está seguro no meu disco".

### Configuração (`config.toml`)

```toml
vault_path   = "C:/Users/kakam/OneDrive/Área de Trabalho/Reading"
inbox_folder = "Inbox"
audio_store  = "C:/Users/kakam/.handy-bridge/audio"
port         = 8787

[asr]
handy_exe = "C:/Users/kakam/AppData/Local/Handy/handy.exe"
model     = "handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf/nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf"
timeout_s = 900

[post_process]
enabled  = true
backend  = "claude_cli"   # claude_cli | anthropic_api | ollama | none
model    = "claude-haiku-4-5-20251001"
```

### Desempenho medido nesta máquina

| Medida | Valor |
|---|---|
| GPU vinculada | GeForce MX110 (backend Vulkan0) |
| Velocidade de transcrição | `rtf` 1.33 — processar leva ~0,75× a duração do áudio |
| Carga do modelo | ~1,4 s por invocação |
| Nota de 1 min (estimado) | ~46 s de transcrição |
| `claude -p` com Opus | $0,153 equivalente por nota, ~3 s |
| `claude -p` com Haiku | $0,060 equivalente por nota, ~5 s |

O custo por nota do CLI vem de ~30 mil tokens de system prompt do Claude Code que
viajam a cada chamada, para uma tarefa que precisa de ~200 tokens de contexto. Em
plano de assinatura isso não vira cobrança, mas consome limites de uso. É a razão de
`PostProcessor` ser plugável: trocar para `anthropic_api` reduz o custo em ~100× sem
tocar no resto do bridge.

### Degradação

Se o `claude` falhar ou estiver indisponível, a nota é escrita mesmo assim, com a transcrição crua no corpo e título por timestamp. **Uma falha no passo de LLM nunca pode custar uma nota.**

## 7. Formato da nota

Arquivo: `Inbox/2026-09-07 1432 - Discordo da tese sobre agricultura.md`

```markdown
---
title: "Discordo da tese sobre agricultura"
date: 2026-09-07T14:32:11
duration: 47s
source: rsvp-nano
asr_model: nemotron-3.5-asr-streaming-0.6b
book: "[[epdf.pub_sapiens-uma-breve-historia-da-humanidade]]"
word_offset: 12438
tags: [ideia, leitura/sapiens]
---

Texto limpo pelo LLM, sem hesitações e com pontuação.

> [!quote] Trecho que eu estava lendo
> ...a Revolução Agrícola foi a maior fraude da história...

> [!note]- Transcrição original
> Texto cru do Nemotron, palavra por palavra.
```

`book`, `word_offset` e o callout de trecho aparecem apenas quando a gravação nasceu
dentro do leitor. Uma nota solta (fora da leitura) omite os três.

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
| **Contenção de SD durante a leitura.** Gravar de dentro do leitor faz o áudio escrever no mesmo cartão de onde o livro é lido, e o SD está em modo 1-bit (`kSdData1/2/3Pin = GPIO_NUM_NC`), o mais lento. A 32 KB/s contínuos, isso pode causar falhas na gravação. | Alta | Risco criado pela revisão de escopo de 2026-09-08 — não existia quando a gravação era numa tela separada com o livro descarregado. O buffer duplo em PSRAM absorve picos, mas **isto tem que ser medido antes de qualquer polimento de UI.** |
| Latência de ~250 ms em todo play/pause | Média | Consequência direta de D7. Se incomodar na prática, o fallback é mover o gatilho para swipe vertical, que não exige supressão. |
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

**Sub-projeto 2 — identidade visual.** O tema é trivial: 18 tokens de cor num
`themes/<nome>.toml` novo mais uma entrada em `themes/index.json`, conflito zero com
upstream. A **fonte não é trivial** — existe um formato próprio (`RFont4`) com
compilador em `fonts/convert_alpha4_font.py`, que gera quatro strikes
(large/medium/small/compact, padrão 52/43/33/14 px) a partir de um TTF. Três
restrições: a fonte precisa de licença redistribuível (as atuais trazem `OFL.txt`), as
escolhas atuais são todas otimizadas para legibilidade e não para estética — num
regime de uma palavra por vez com âncora fixa, fonte bonita costuma ler pior — e
métricas diferentes exigem override de tamanhos. Depende do harness de preview (D15)
para ser iterável.

**Sub-projeto 3 — vocabulário com repetição espaçada.** Marcar uma palavra desconhecida
durante a leitura, que entra na mesma fila do SD e chega ao vault como flashcard. Reusa
fila, uploader e bridge; muda o endpoint e o formato da nota.

**Sub-projeto 4 — painel de estatísticas de leitura.** WPM ao longo do tempo, palavras
por dia, sequência de dias. Independente: não precisa de áudio nem de rede. A faixa de
640×172 é a forma natural de uma sparkline.

**Escopo futuro, sem plano.** Bichinho virtual estilo Tamagotchi (o fork RSVPbookworm
tem uma implementação completa cuja **ideia** vale, mas cujo código não serve: bifurcou
da v0.0.1, usa `driver/i2s.h` obsoleto, e o HEAD não compila porque
`TimeService::setManualTime` é chamado sem ser declarado). Jogos que caibam em 640×172 —
os melhores encaixes na proporção 3,7:1 são runner lateral de um botão, bola em
corredor por inclinação usando o QMI8658, e breakout ultralargo.

**Sub-projeto 3 — conversa por voz.** Em vez de Gemini Live (que exigiria WebSocket, TLS e áudio bidirecional em tempo real no firmware, além da API key morando no device), a proposta é walkie-talkie: o device grava a pergunta, o bridge transcreve, o `claude` CLI responde **com o vault Reading como working directory**, e um TTS no PC devolve o áudio para o ES8311 tocar. Reusa toda a infraestrutura deste documento e entrega algo melhor — um agente que enxerga as próprias notas do usuário.
