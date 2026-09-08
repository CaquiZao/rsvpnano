# handy-bridge

Serviço local que recebe gravações do RSVP Nano, transcreve com o
[Handy](https://github.com/cjpais/Handy) e escreve notas Markdown no vault do Obsidian.

O áudio nunca sai da sua máquina: a transcrição roda localmente com o modelo Nemotron
Streaming 3.5. Apenas o texto já transcrito é enviado ao passo de pós-processamento,
e mesmo esse passo é plugável — veja [Pós-processamento](#pós-processamento).

## Como funciona

```
device → POST /v1/notes → WAV salvo em disco → 200 imediato
                              ↓ (fila em background, uma nota por vez)
                          handy --transcribe-file
                              ↓
                          título, tags e limpeza
                              ↓
                          Reading/Inbox/*.md
```

O `200` sai **antes** da transcrição, de propósito: o device não deve manter a rádio
ligada esperando inferência. O ACK significa apenas "o áudio está seguro no meu disco".

O serviço se anuncia na rede como `_handybridge._tcp` via mDNS, então o firmware o
encontra sem você configurar endereço IP em lugar nenhum.

## Requisitos

- Python 3.13+ e [uv](https://docs.astral.sh/uv/)
- [Handy](https://github.com/cjpais/Handy) instalado, **com o modelo já baixado**
  (`--transcribe-file` não baixa modelo)
- `claude` CLI no PATH, se você usar o pós-processamento padrão

## Instalação

```bash
cd bridge
uv sync
cp config.example.toml config.toml
```

Edite o `config.toml` com os caminhos da sua máquina. Ele é ignorado pelo git porque
contém caminhos absolutos.

Para descobrir o id exato do seu modelo:

```bash
"C:/Users/kakam/AppData/Local/Handy/handy.exe" --list-models --json
```

O id inclui o nome do arquivo `.gguf`, não apenas o repositório — por exemplo
`handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf/nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf`.

## Uso

```bash
uv run python -m handy_bridge --config config.toml --log-level info
```

Testar sem o device:

```bash
curl -X POST http://localhost:8787/v1/notes \
  -F "audio=@nota.wav;type=audio/wav;filename=20260908-115700.wav" \
  -F 'meta={"clock_synced":true,"recorded_at":"2026-09-08T11:57:00"}'
```

O WAV precisa ser **PCM 16 kHz mono 16-bit** — é o que o device grava e o que o Handy
exige. Para converter: `ffmpeg -i entrada.m4a -ar 16000 -ac 1 -c:a pcm_s16le nota.wav`.

## Testes

```bash
uv run pytest
```

Tudo roda sem hardware, sem rede e sem gastar tokens: o Handy e o `claude` são
substituídos por dublês nos testes.

## Pós-processamento

Controlado pela seção `[post_process]` do `config.toml`:

| backend | O que faz |
|---|---|
| `claude_cli` | Chama o CLI `claude` local. Padrão — zero configuração se você já o tem. |
| `anthropic_api` | Chamada direta à API. ~100× mais barato por nota e mais rápido, mas exige API key. Ainda não implementado. |
| `ollama` | Modelo local, nada sai da máquina. Ainda não implementado. |
| `none` | Sem pós-processamento: a nota chega com a transcrição crua e título por timestamp. |

**Aviso de custo.** O `claude -p` carrega todo o system prompt do Claude Code a cada
chamada — cerca de 30 mil tokens para uma tarefa que precisa de ~200. Medido nesta
máquina: **$0,153 equivalente por nota com Opus, $0,060 com Haiku**. Em plano de
assinatura isso não vira cobrança em dólar, mas consome limites de uso. É a razão de o
backend ser plugável.

Uma falha no pós-processamento **nunca** custa uma nota: se o `claude` falhar, a nota é
escrita com a transcrição crua e título por timestamp.

## Formato da nota

```markdown
---
title: "Discordo da tese sobre agricultura"
date: 2026-09-08T11:57:00
duration: 21s
source: rsvp-nano
asr_model: .../nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf
book: "[[epdf.pub_sapiens-uma-breve-historia-da-humanidade]]"
word_offset: 12438
tags: [leitura, agricultura]
---

Texto limpo pelo LLM.

> [!quote] Trecho que eu estava lendo
> ...a Revolução Agrícola foi a maior fraude da história...

> [!note]- Transcrição original
> transcrição literal do Nemotron, palavra por palavra
```

A **transcrição crua fica sempre preservada** no callout recolhido. O LLM gera título,
tags e a versão limpa, mas nunca substitui o que você falou — se ele alucinar, o
original está a um clique.

Os campos `book`, `word_offset` e o callout de trecho só aparecem quando a gravação
nasceu dentro do leitor. Uma nota solta omite os três.

Escrita é **atômica** (arquivo temporário + rename) porque o vault vive dentro do
OneDrive e escrita parcial pode virar cópia de conflito.

## Pendências viram cartões no Kanban

A mesma chamada de LLM que gera título e tags também extrai pendências, cada uma
classificada por como você a expressou:

| Como você falou | Raia do quadro |
|---|---|
| Disse uma palavra marcadora — *pendência*, *tarefa*, *anotar* — seguida de uma ação | **A pesquisar** |
| Não disse a palavra, mas havia intenção clara de estudar algo | **Triagem** |
| Era uma dúvida conceitual e o bridge já respondeu | **Concluído** |

A extração é **deliberadamente conservadora**: em dúvida entre pendência e comentário,
o modelo omite. Um quadro com pendências inventadas é pior que um quadro incompleto,
porque você para de confiar nele.

Um quadro por livro em `Quadros/<livro>.md`; notas gravadas fora da leitura vão para
`Quadros/Geral.md`. Cada cartão linka de volta para a nota, então o contexto não se
perde. O formato do arquivo foi extraído do código do plugin obsidian-kanban 2.0.51, e
a inserção é por linha — o bloco `%% kanban:settings` nunca é tocado.

## Respostas automáticas no Telegram

Quando uma pendência é uma dúvida conceitual respondível de imediato, o bridge responde
em no máximo ~120 palavras, **ancorada no trecho do livro que você estava lendo**, grava
a resposta na própria nota num callout `[!question]` e manda para o seu Telegram.

Todas as perguntas de uma nota são respondidas em **uma única chamada**. Uma chamada por
pergunta multiplicaria os ~30 mil tokens de system prompt que cada invocação do
`claude -p` carrega.

Para ativar: crie um bot com o [@BotFather](https://t.me/botfather) (`/newbot`), cole o
token no `config.toml` e descubra seu `chat_id` falando com o bot e abrindo
`https://api.telegram.org/bot<TOKEN>/getUpdates`. Sem token, tudo o mais funciona — só
não há entrega no celular.

**Toda resposta automática vai com um aviso** de que não foi verificada. Uma resposta que
chega sozinha no celular é lida com menos ceticismo que uma que você foi buscar, e em
qualquer coisa factual recente ou numérica o risco de erro é real.

## Livro como contexto

Quando uma nota chega com `book`, o bridge converte o `.epub` correspondente do vault
para markdown em `Books/`, uma vez por livro. Isso dá ao
[Claudian](https://github.com/YishenTu/claudian) o livro inteiro como texto
pesquisável, para perguntas que o trecho embutido na nota não alcança.

## Desempenho medido

Nesta máquina (GeForce MX110, backend Vulkan), com uma nota real de **21 s** de fala
em português:

| Medida | Valor |
|---|---|
| ACK do `POST /v1/notes` | **0,26–0,28 s** |
| Nota pronta no vault | **23,5 s** (modelo em cache) a **30,6 s** (primeira execução) |
| Velocidade de transcrição | `rtf` 1,33 — processar leva ~0,75× a duração do áudio |
| Carga do modelo | ~1,4 s por invocação |
| `claude -p` com Haiku | ~5 s |

O ACK é o número que importa para o firmware: o device recebe a confirmação em ~0,26 s
e pode desligar a rádio imediatamente, sem esperar os ~25 s de inferência.

Regra de bolso para estimar: **duração do áudio × 0,75 + ~7 s** de sobrecarga fixa
(carga do modelo mais a chamada ao LLM).
