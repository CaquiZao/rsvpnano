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
                          título, tags, tipo e limpeza
                              ↓
                          Livros/<livro>/<tipo>/*.md
                              ↓
                          resumo do capítulo e do livro
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

A primeira linha do log é `handy-bridge starting, pid N`. Guarde esse número: o
Python do venv roda atrás de um processo trampolim, então **matar o pai deixa o
filho vivo** segurando a porta 8787 e fazendo poll do Telegram. Para parar de
verdade, mate os dois:

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
  Where-Object { $_.CommandLine -like "*handy_bridge*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Subir com a porta já ocupada não dá traceback: o serviço checa antes e diz o que
fazer. Isso também evita a janela em que dois bridges fazem poll do mesmo bot — o
Telegram responde **409** enquanto ela durar, e por até 25 s depois de um kill,
porque o long-poll interrompido continua valendo do lado dele. Um 409 isolado
logo após reiniciar é esperado; um 409 que se repete por minutos não é.

Testar sem o device:

```bash
curl -X POST http://localhost:8787/v1/notes \
  -F "audio=@nota.wav;type=audio/wav;filename=20260908-115700.wav" \
  -F 'meta={"clock_synced":true,"recorded_at":"2026-09-08T11:57:00"}'
```

O WAV precisa ser **PCM 16 kHz mono 16-bit** — é o que o device grava e o que o Handy
exige. Para converter: `ffmpeg -i entrada.m4a -ar 16000 -ac 1 -c:a pcm_s16le nota.wav`.

## Quando uma nota é recusada

O `POST /v1/notes` responde `400` quando o meta não é JSON, quando o WAV não é
utilizável, ou quando a gravação tem menos de um segundo. Nesses casos o áudio **não é
apagado**: vai para `<audio_store>/rejected/`, e o motivo entra no log com o id da nota.

A recusa é o momento em que a gravação vale mais, não menos. O device lê só a linha de
status da resposta — de propósito, drenar o corpo custaria tempo de rádio — então um
motivo que viaja apenas no JSON de resposta não chega a ninguém. E o device trata
qualquer 4xx como definitivo, tirando a nota da fila. Enquanto as duas pontas
descartavam a própria cópia, uma recusa apagava a gravação dos dois lados e ainda
reportava sucesso na tela.

Quando é o meta que está quebrado, os bytes exatos que o firmware enviou ficam ao lado
do áudio num `<id>.meta.txt`. "Não é JSON válido" diz qual camada falhou e nada sobre
como.

Duas recusas acontecem **antes** do endpoint rodar: corpo que o parser não consegue ler
(`400`) e formulário com campo faltando (`422`). Essas também vão para o log, com o
`content-type` e o tamanho declarado do corpo, porque o que quebra nelas é o framing do
multipart e não o conteúdo.

No device, uma nota recusada é **estacionada** em vez de apagada: o `.wav` e o `.json`
ganham o sufixo `.parked` em `/voice` no cartão, ficam fora da fila e nunca são
varridos. A tela diz "Bridge recusou a nota" em vez de contar a recusa como entrega.

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

## Como o vault é organizado

```
Reading/
├── Livros/
│   └── Sapiens/
│       ├── Sapiens.md            ← resumo do livro     (derivado)
│       ├── Capítulos/
│       │   └── 04 - Os Navegadores.md   (derivado)
│       ├── Anotações/            ← uma pasta por tipo de nota
│       │   └── 2026-09-08 1205 - Crítica à tese sobre agricultura.md
│       ├── Perguntas/
│       ├── Recall/
│       ├── Quadro.md             ← pendências
│       └── fonte/                ← epub, markdown convertido, índice
└── Geral/                        ← notas gravadas fora da leitura
```

Três eixos, três mecanismos: **pastas por tipo** para achar uma anotação, o **resumo
do capítulo** para a posição de leitura, o **quadro do Kanban** para o status. As três
pastas existem sempre, mesmo vazias — uma pasta ausente é invisível, e abrir um livro
sem ver `Recall` deixa o recall sem lugar óbvio para cair.

O nome do arquivo é **a data e a hora da gravação**. A posição de leitura vive no
frontmatter (`chapter`, `word_offset`), que é de onde os resumos a leem. O capítulo sai
da busca do trecho no texto convertido, nunca de aritmética de offset — o device conta
palavras com o tokenizador dele e o bridge com o próprio, e os dois nunca vão bater.

Pasta por tipo tem um custo, e ele é real: as notas de um mesmo capítulo ficam
espalhadas em três pastas, e o nome do arquivo não diz onde no livro elas estão. É o
**resumo do capítulo** que devolve essa visão, com as três seções juntas e em ordem de
leitura — ordenadas por `word_offset`, não por data de gravação.

As notas são **fonte**: escritas uma vez, nunca reescritas. Os resumos são
**derivados**, reconstruíveis a partir delas. Isso não é organização, é segurança:
uma cópia de conflito do OneDrive num derivado não custa nada, porque basta gerar de
novo.

Migrar um vault do layout antigo:

```bash
uv run python -m handy_bridge.migrate --vault "<caminho>"   # dry-run
uv run python -m handy_bridge.migrate --vault "<caminho>" --apply
```

## Formato da nota

```markdown
---
title: "Discordo da tese sobre agricultura"
kind: recall
date: 2026-09-08T11:57:00
duration: 21s
source: rsvp-nano
asr_model: .../nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf
tags: [leitura, agricultura]
book: "[[epdf.pub_sapiens-uma-breve-historia-da-humanidade]]"
word_offset: 12438
chapter: 8
chapter_title: "A maior fraude da história"
chapter_source: exato
---

Texto limpo pelo LLM.

> [!quote] Trecho que eu estava lendo
> ...a Revolução Agrícola foi a maior fraude da história...

> [!success] Conferência do que você lembrou
>
> ✓ **Você disse:** A agricultura piorou a vida do indivíduo
>
> ✗ **Você disse:** A população caiu depois da agricultura
> **Na verdade:** A população cresceu; a qualidade de vida individual caiu
>
> *Conferência automática, não verificada.*

> [!note]- Transcrição original
> transcrição literal do Nemotron, palavra por palavra
```

A **transcrição crua fica sempre preservada** no callout recolhido. O LLM gera título,
tags e a versão limpa, mas nunca substitui o que você falou — se ele alucinar, o
original está a um clique.

Os campos de âncora (`book`, `word_offset`, `chapter*`) e o callout de trecho só
aparecem quando a gravação nasceu dentro do leitor. `kind` aparece sempre: é o eixo
que as views filtram, e uma nota sem ele ficaria invisível nas três.

`chapter_source` diz **como** o capítulo foi descoberto — `exato` quando o trecho
casou no texto do livro, `estimado` quando caiu na estimativa por offset. O campo
existe para que uma nota mal posicionada continue identificável depois.

Escrita é **atômica** (arquivo temporário + rename) porque o vault vive dentro do
OneDrive e escrita parcial pode virar cópia de conflito.

## Tipo da nota

`kind` é `anotação`, `pergunta` ou `recall`, decidido em duas camadas:

1. **O LLM infere**, num campo da chamada de pós-processamento que já existe — custo
   marginal zero.
2. **A palavra falada vence.** Dizer `recall`, `recal`, `ricol` ou `recapitulando`
   força `kind: recall`, independente do que o modelo achou.

As três primeiras variantes existem porque o Nemotron transcreve português e "recall"
é palavra inglesa no meio da fala: o casamento é texto puro, então aceitar variação
fonética custa zero e evita que o override falhe por sotaque. `recapitulando` é a
alternativa que não depende disso.

## Conferência do recall

Quando a nota é um recall, o bridge compara o que você falou com **o trecho que você
leu desde o último recall**, limitado ao capítulo atual nos dois extremos. O limite é
o que impede uma sessão longa de leitura sem gravação de transformar a comparação em
meio livro.

Os erros aparecem **lado a lado no corpo** da nota, não escondidos: se o vault é
material de auto-teste, o que te derrubou é a parte que vale reencontrar depois.

Toda conferência vai com aviso de não verificada, pela mesma razão das respostas
automáticas.

## Resumos de capítulo e de livro

Dois arquivos derivados por livro, alimentados **só pelo que você registrou** — o
texto limpo das notas, os pares pergunta/resposta e as correções de recall. O texto
do livro nunca entra num resumo. Isso tem uma consequência que vale saber: o resumo
espelha o que você engajou, não o que o livro diz, e capítulo lido sem gravação não
gera resumo nenhum. A lacuna é informação, e o resumo do livro lista os capítulos sem
registro.

O **resumo do capítulo** tem uma seção `## Minhas observações` que o bridge lê,
preserva e reescreve intacta. É onde você pode anotar à mão dentro de um arquivo
gerado sem perder na próxima gravação. Tudo fora dela é sobrescrito.

O **resumo do livro** é reconstruído a partir das seções `## Síntese` dos capítulos,
não das notas cruas. Duas razões: cada síntese tem teto de 400 palavras, então a
entrada cresce com o número de capítulos e não com o volume de gravação; e
reconstruir sobre o conjunto é o que permite **rebalancear** os pontos importantes —
se o capítulo 9 mostra que o que parecia central no 2 era secundário, um resumo
reconstruído corrige a hierarquia, um que só acumula não.

**Limitação conhecida.** O bridge só descobre onde você está quando uma nota chega,
então o resumo do livro é reconstruído na primeira nota do capítulo seguinte, não
quando você de fato termina o capítulo. Terminar o capítulo 4 e só gravar no 7 faz o
resumo pular direto.

**Custo.** Com `chapter_on = "nota"` o resumo do capítulo é regenerado a cada
gravação, o que custa uma chamada de `claude -p` por nota — cerca de $0,06 com Haiku,
pelas mesmas razões de system prompt descritas acima. Com `chapter_on = "capitulo"`
cai para aproximadamente uma chamada por capítulo, ao custo de o resumo ficar
desatualizado enquanto você ainda está dentro dele.

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

Um quadro por livro em `Livros/<livro>/Quadro.md`; notas gravadas fora da leitura vão
para `Geral/Quadro.md`. Cada cartão linka de volta para a nota, então o contexto não
se perde. O formato do arquivo foi extraído do código do plugin obsidian-kanban
2.0.51, e a inserção é por linha — o bloco `%% kanban:settings` nunca é tocado.

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

## Lembrete semanal

Uma vez por semana o bridge manda no Telegram o que continua aberto nas raias
**Triagem** e **A pesquisar** de todos os quadros. Cartões marcados e as raias
*Pesquisando* e *Concluído* ficam de fora.

Isso existe por um motivo específico: a raia Triagem recebe as pendências que o modelo
**inferiu**, não as que você pediu explicitamente. Sem um empurrão periódico ela
acumula, e um quadro que você não revisita é um quadro em que você deixa de confiar.

Configurado em `[digest]` — dia da semana, hora, e um interruptor. Se a máquina estiver
desligada na hora marcada, ele manda mais tarde no mesmo dia: lembrete atrasado ainda
vale mais que lembrete nenhum.

## Livro como contexto

Quando uma nota chega com `book`, o bridge converte o `.epub` correspondente para
markdown em `Livros/<livro>/fonte/`, uma vez por livro, e indexa os capítulos ao lado
num `.chapters.json`. O markdown dá ao
[Claudian](https://github.com/YishenTu/claudian) o livro inteiro como texto
pesquisável, para perguntas que o trecho embutido na nota não alcança; o índice é o
que resolve o capítulo de cada nota e recorta a passagem da conferência de recall.

Um detalhe do índice que vale saber: os capítulos vêm do spine do epub, que conta capa,
folha de rosto e sumário. Então o capítulo 1 de um livro pode aparecer numerado como
`03`. A ordenação e o título ficam corretos; só o número não corresponde ao do livro
impresso. Renumerar por heurística de front matter erraria em silêncio, o que é pior.

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
