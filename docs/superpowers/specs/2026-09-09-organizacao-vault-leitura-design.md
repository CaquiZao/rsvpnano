# Organização do vault e fluxo de leitura

**Data:** 2026-09-09
**Status:** implementado, com duas decisões revertidas em uso — ver §15
**Depende de:** `2026-09-07-notas-de-voz-obsidian-design.md`
**Escopo de código:** apenas `bridge/` e o vault. Nenhuma mudança de firmware.

## 1. Problema

O bridge já entrega notas de voz transcritas no vault, mas o vault não sustenta o jeito
como a leitura de fato acontece. Estado real hoje, com 5 notas e 1 quadro:

1. **`Inbox/` nunca esvazia.** Tem nome de triagem e é a casa definitiva.
2. **Cada gravação é um arquivo que mistura três coisas.** A nota de 2026-09-09 22:05
   contém, no mesmo `.md`: o que a pessoa entendeu, três perguntas com respostas, e uma
   pendência.
3. **Nada distingue recall de dúvida.** "Vou falar o que entendi para ver se entendi" e
   "não entendi, me explica" são intenções opostas e caem no mesmo lugar. O bridge
   classifica *pendências*, não classifica *notas*.
4. **Cartões duplicados no quadro.** As notas de 2026-09-08 14:00 e 16:10 geraram
   "Entender o que exatamente foi o Big Bang" duas vezes, uma marcada e outra não.
5. **Um livro vive em quatro lugares:** `Inbox/<livro>/`, `Quadros/<livro>.md`,
   `Books/<livro>.md` e o `.epub` na raiz.
6. **Não existe síntese.** Nada responde "Sapiens: onde estou, o que aprendi, o que
   ficou aberto". Para revisar um capítulo é preciso abrir cada nota.

A prática que o vault precisa servir, nas palavras do usuário: ao ler, ele anota e **fala
o que entendeu para verificar se entendeu e se lembrou**. As três coisas que ele faz ao
voltar ao vault dias depois:

- **testar se ainda lembra** — o vault é material de auto-teste;
- **reler o livro pelas próprias notas**, na ordem em que leu;
- **caçar o que ficou em aberto**.

Escrever textos a partir das notas foi explicitamente descartado. Isso elimina notas
atômicas por ideia e links entre livros — a parte mais custosa, e que não serve a nada
aqui.

## 2. O aperto central

Os três usos cortam por eixos diferentes: **tipo** (anotação / pergunta / recall),
**posição** (ordem de leitura) e **status** (aberto / fechado). Pasta só corta de um
jeito. A decisão de fundo deste documento é atribuir cada eixo ao mecanismo que o serve,
sem duplicar dado:

| Eixo | Mecanismo | Por quê |
|---|---|---|
| Tipo | pastas em disco | ler uma anotação é um clique no explorador, não uma consulta |
| Posição | resumo do capítulo | é onde as notas de um capítulo voltam a ficar juntas |
| Status | quadro do Kanban | já existe e já funciona |

Esta tabela foi invertida depois de o usuário usar o resultado: a versão original punha
posição em pasta e tipo em views de Bases. Ver §15.

## 3. Escopo

**Dentro:**

- Layout novo do vault, um diretório por livro
- Campo `kind` no frontmatter, com detecção por LLM e override por palavra falada
- Resolução de capítulo pelo texto do trecho, com `word_offset` como desempate
- Correção de recall na hora, ancorada no intervalo lido desde o último recall
- Resumo por capítulo, derivado, regenerado a cada nota daquele capítulo
- Resumo do livro, derivado, reconstruído a cada capítulo concluído
- Três arquivos `.base` na raiz como porta de entrada por tipo
- Script de migração idempotente das notas e do quadro atuais

**Fora:**

- Mudanças de firmware de qualquer natureza
- Revisão espaçada com agendador próprio — o digest semanal que já existe basta
- Notas atômicas por ideia, links entre livros, grafo de conceitos
- Backends de pós-processamento novos (`anthropic_api` e `ollama` seguem pendentes)
- Progresso de leitura reportado independente de gravação (ver §9)

## 4. Layout do vault

```
Reading/
├── Anotações.base                        ← porta de entrada por tipo
├── Perguntas.base
├── Recall.base
├── Livros/
│   └── Sapiens/
│       ├── Sapiens.md                    ← resumo do livro     (derivado)
│       ├── Capítulos/
│       │   ├── 01 - O Animal Insignificante.md   (derivado)
│       │   └── 04 - Os Navegadores.md            (derivado)
│       ├── Notas/
│       │   ├── 01-001324 Definição e ordem entre física e química.md
│       │   └── 04-012438 Crítica à tese sobre revolução agrícola.md
│       ├── Quadro.md                     ← pendências
│       └── fonte/
│           ├── Sapiens.epub
│           └── Sapiens.md                ← markdown convertido, para busca
└── Geral/
    ├── Notas/
    └── Quadro.md
```

### 4.1 Fonte e derivado

A distinção mais importante do layout:

- **`Notas/` é fonte.** Escrita uma vez, atomicamente, nunca reescrita.
- **`Sapiens.md` e `Capítulos/*.md` são derivados.** Reconstruíveis a partir das notas.

Isso não é organização, é segurança. O vault vive no OneDrive, e o spec anterior já
adotou escrita atômica porque escrita parcial ali vira cópia de conflito. Um derivado
pode ser reescrito à vontade justamente porque uma cópia de conflito nele não custa nada:
apaga e gera de novo. Foi essa propriedade que permitiu aceitar resumos por capítulo, que
são arquivos reescritos com frequência.

Derivados levam `generated: true` no frontmatter e um aviso de uma linha.

### 4.2 Nome do arquivo de nota

`YYYY-MM-DD HHMM - <título>.md`.

A versão original deste spec usava `<capítulo>-<offset> <título>.md`, para que uma
listagem saísse em ordem de leitura. Isso valia enquanto todas as notas dividiam um
diretório; com as notas separadas por tipo (§10), a ordem de leitura passou a viver no
resumo do capítulo e o nome ficou carregando um código ilegível. A posição vive em
`chapter` e `word_offset`, que é de onde os resumos a leem.

### 4.3 Frontmatter da nota

Campos novos sobre o que já existe:

| Campo | Valores | Significado |
|---|---|---|
| `kind` | `anotação` \| `pergunta` \| `recall` | ver §5 |
| `chapter` | inteiro | índice do capítulo, base 1 |
| `chapter_title` | texto | título extraído do spine |
| `chapter_source` | `exato` \| `estimado` | como o capítulo foi resolvido, ver §6 |

`chapter_source: exato` significa que o trecho casou no texto convertido; `estimado`
significa que caiu na degradação por `word_offset`. O campo existe para que uma nota
mal-posicionada seja identificável depois sem re-rodar nada.

Os demais campos (`title`, `date`, `duration`, `source`, `asr_model`, `book`,
`word_offset`, `tags`, `date_estimated`) seguem inalterados.

## 5. Tipo da nota

`kind: anotação | pergunta | recall`.

Detecção em duas camadas, conforme decidido:

1. **O LLM infere.** Um campo novo no JSON que a chamada de pós-processamento já
   devolve — custo marginal zero, sem chamada extra.
2. **A palavra falada vence.** Uma checagem em código sobre a transcrição bruta força
   `kind: recall`, independente do que o LLM disse.

As variantes aceitas são `recall`, `recal`, `ricol` e `recapitulando`. O motivo das três
primeiras: o Nemotron transcreve português, e "recall" é palavra inglesa no meio da fala —
o casamento é comparação de texto, então aceitar variantes fonéticas custa zero e evita
que o override falhe por sotaque. `recapitulando` existe como alternativa que não depende
disso.

As duas camadas se complementam: o prompt tolera variação que o regex não previu, o regex
garante o determinismo que o usuário pediu ("as duas, marcadora manda").

As palavras marcadoras de pendência (`pendência`, `tarefa`, `anotar`) seguem como estão em
`postprocess/claude_cli.py`, e continuam alimentando o quadro, não o `kind`.

## 6. Resolução de capítulo

O ponto mais frágil do desenho, e por isso o mais explícito.

**O problema.** Capítulo, nome de arquivo, resumo e intervalo de correção todos dependem
de saber em que capítulo a gravação nasceu. O `word_offset` é contado pelo device, que
constrói seu próprio índice a partir do epub. Se o bridge re-tokenizar o epub em Python,
as contagens divergem — conversão de HTML, regras de espaço em branco e tratamento de
pontuação diferentes — e o offset cai no capítulo errado. Um erro silencioso aqui
envenena tudo o que vem depois.

**A solução.** O device já manda o texto do trecho. `App.cpp` preenche `anchor.excerpt`
com o parágrafo da posição atual, `VoiceNoteMeta.cpp` o serializa, e o bridge já o lê em
`pipeline.py`. Então o capítulo se resolve por **busca de texto**, não por aritmética de
offset:

1. Normalizar o trecho e o markdown convertido — colapsar espaços, remover pontuação,
   *casefold*.
2. Procurar o trecho normalizado no livro normalizado. A posição de caractere encontrada
   dá o capítulo exatamente, seja qual for a tokenização de cada lado.
3. **Desempate por `word_offset`** quando o trecho casa em mais de um lugar: escolher a
   ocorrência mais próxima da posição estimada pelo offset. É o uso em que um offset
   aproximado é confiável.
4. **Degradação:** sem casamento, estimar o capítulo pelo `word_offset` proporcional à
   contagem de palavras do bridge e marcar `chapter_source: estimado`. Sem livro ou sem
   trecho, a nota vai para `Geral/` e não entra em resumo nenhum.

O trecho é truncado por `clampExcerpt`, o que não atrapalha: busca por prefixo basta.

**Validação obrigatória antes de implementar o resto.** Rodar a resolução contra as 5
notas reais que já existem no vault e conferir capítulo a capítulo. Se a taxa de casamento
exato não for de 5/5, o desenho precisa de uma conversa nova antes de seguir —
possivelmente passando o capítulo no metadado do device, que é mudança pequena de firmware
mas sai do escopo deste documento.

O índice de capítulos precisa ser extraído na conversão. `epub.py` hoje produz markdown
plano; `_spine_hrefs` já dá a ordem do spine, que é a fronteira natural de capítulo.
`convert_to_markdown` passa a emitir, junto, uma lista de `(índice, título, posição)`.

## 7. Correção de recall

Quando `kind: recall`, o bridge compara o que foi falado com o que foi lido e devolve onde
bateu, onde faltou e o que foi invertido.

**Intervalo comparado:** do `word_offset` do último recall do mesmo livro até o offset
atual, **nunca começando antes do início do capítulo atual**. É literalmente "o que eu li
desde a última vez que falei", e se autolimita: nunca passa de um capítulo, mesmo depois
de muita leitura sem gravar. Sem recall anterior, começa no início do capítulo.

**Entrega:** callouts na nota e mensagem no Telegram, seguindo o mecanismo que já existe
para respostas. Vale o mesmo aviso de resposta não verificada que o spec anterior adotou,
e pela mesma razão: correção que chega sozinha no celular é lida com menos ceticismo que
correção que se foi buscar.

**Custo:** a correção pega carona na chamada de respostas que já roda em
`pipeline.py:100` — nenhuma chamada nova. Uma nota de recall segue custando as mesmas
duas chamadas de `claude -p` que qualquer nota custa hoje. A única exceção em todo o
desenho é o gatilho de fim de capítulo da §9.

Este é o único lugar em que o texto do livro alimenta uma saída de LLM. Os resumos não o
usam — ver §8.

## 8. Resumo do capítulo

Arquivo derivado em `Capítulos/NN - Título.md`, regenerado a cada nota que cai naquele
capítulo.

**Fontes permitidas, e só elas:** o texto limpo das notas, os pares pergunta/resposta
(inclusive os entregues no Telegram) e as correções de recall. **O texto do livro não
entra.** Isso é requisito explícito do usuário, e tem uma consequência que vale nomear: o
resumo espelha o que ele registrou, não o que o livro diz. Capítulo lido sem gravação não
gera resumo, e essa ausência é informação — é onde ele leu sem engajar.

**Estrutura:**

1. `## Síntese` — no máximo ~400 palavras. O limite não é estético: é esta seção, e só
   ela, que alimenta o resumo do livro (§9). Sem teto aqui, a entrada do resumo do livro
   cresce sem controle conforme o livro avança.
2. `## Anotações`
3. `## Perguntas e respostas`
4. `## Recall` — cada ponto **lado a lado**: "eu entendi X / na verdade Y", com peso igual
   no corpo. O usuário escolheu isso sobre a versão limpa por uma razão coerente: se o
   vault é material de auto-teste, o erro registrado vale mais que a leitura corrida. O
   custo aceito é resumo mais longo.
5. `## Minhas observações` — **seção reservada**. O bridge lê o arquivo antigo, preserva
   este bloco e o reescreve intacto. É o que permite anotar à mão dentro de um derivado
   sem perder na próxima gravação.

**Custo:** a regeneração entra na chamada de pós-processamento que já roda por nota. O
README mostra que o custo é dominado pelos ~30 mil tokens de system prompt do `claude -p`,
não pelo conteúdo — somar as notas anteriores do capítulo, que são curtas, é acréscimo
marginal. "Atualizar automaticamente a cada registro" não multiplica chamadas.

**Falha nunca custa uma nota.** Mesma regra do spec anterior: se a regeneração falhar, a
nota é escrita e o resumo antigo permanece. Um resumo velho é melhor que uma nota perdida.

## 9. Resumo do livro

Arquivo derivado em `Livros/<livro>/<Livro>.md`.

**Reconstruído a partir da seção `## Síntese` de cada resumo de capítulo**, não das notas
cruas nem do resumo de capítulo inteiro. Duas razões:

1. **Custo estável.** Cada síntese tem teto de ~400 palavras (§8), então a entrada é
   proporcional ao número de capítulos e não ao volume de gravação. Um livro de 20
   capítulos entra com ~8 mil palavras, previsíveis. As notas cruas cresceriam sem teto.
2. **Rebalanceamento.** O usuário pediu "os bullet points mais importantes do livro", e
   importância só se sabe olhando o conjunto. Se o capítulo 9 mostra que o que parecia
   central no 2 era secundário, uma reconstrução corrige a hierarquia; um resumo que só
   acumula, não. É por isso que "cumulativo" aqui significa *reconstruído sobre tudo*, e
   não *acrescentado ao fim*.

**Gatilho:** a chegada de uma nota cujo capítulo é posterior ao maior capítulo já
registrado — isto é, "terminei um capítulo".

**Limitação aceita na v1.** O bridge só descobre a posição de leitura quando uma nota
chega; não há canal de progresso independente. Então o resumo do livro atualiza quando
chega a primeira nota do capítulo seguinte, não quando o capítulo de fato termina.
Terminar o capítulo 4 e só gravar no 7 faz o resumo pular direto. O usuário aceitou isso
para ver se incomoda na prática; resolver de verdade exigiria o device reportar progresso
sem gravação, que é sub-projeto próprio.

**Fecha com a lista de capítulos sem registro.**

Este é o único gatilho que gasta uma terceira chamada de LLM, e ele é raro: uma vez por
capítulo, não uma vez por nota.

## 10. Uma pasta por tipo

`Anotações/`, `Perguntas/` e `Recall/` dentro do diretório de cada livro. As três são
criadas sempre, mesmo vazias: uma pasta ausente é invisível, e abrir um livro sem ver
`Recall` não sugere onde um recall vai cair.

O custo é real e vale nomear: as notas de um mesmo capítulo ficam espalhadas em três
pastas, e o nome do arquivo (§4.2) não diz onde no livro elas estão. O resumo do
capítulo é o que devolve essa visão, ordenado por `word_offset`.

Consequência de implementação: achar as notas de um capítulo exige ler o frontmatter de
todas as notas do livro, em vez de filtrar por prefixo de nome. É irrelevante ao lado
dos ~25 s de transcrição que produziram a nota.

Este spec descrevia originalmente três arquivos `.base` na raiz. Ver §15.

## 11. Migração

Script idempotente, com `--dry-run` obrigatório antes da execução real:

1. `Inbox/<livro>/*.md` → `Livros/<livro>/Notas/`, renomeando para o prefixo de posição.
   Exige resolver o capítulo de cada nota, o que depende da validação da §6.
2. Preencher `kind` nas notas existentes: inferência do LLM sobre o texto limpo, e o
   mesmo override de palavra falada da §5 aplicado à **transcrição bruta preservada** no
   callout `[!note]-`. As notas antigas guardam o original, então o override vale para
   elas exatamente como valeria para uma gravação nova.
3. `Quadros/<livro>.md` → `Livros/<livro>/Quadro.md`, corrigindo os links dos cartões.
4. **Deduplicar** os dois cartões "Entender o que exatamente foi o Big Bang", preservando
   o estado marcado.
5. `Books/<livro>.md` e os `.epub` → `Livros/<livro>/fonte/`. Os epubs ainda não
   convertidos (`rapido e devagar*.epub`) ganham diretório de livro próprio.
6. Gerar os resumos de capítulo e de livro a partir das notas migradas.
7. Criar os três `.base`.
8. Remover `Inbox/`, `Quadros/` e `Books/` **apenas se ficarem vazios**. Sobrando
   qualquer arquivo que o script não soube classificar, o diretório permanece e o script
   o reporta. Migração não apaga o que não entendeu.

O passo 6 gasta LLM sobre as 5 notas existentes. Aceitável de uma vez.

Os `.epub` são movidos, não copiados, e o `Sapiens.epub` tem 4,7 MB — vale rodar o
`--dry-run` com o OneDrive sincronizado antes, para não disputar arquivo com o cliente de
sincronização no meio do movimento.

## 12. Testes

Segue o que o bridge já faz: Handy e `claude` substituídos por dublês, nada de rede, nada
de tokens. Cobertura nova:

- **Resolução de capítulo** — casamento exato, casamento ambíguo resolvido pelo offset,
  degradação para estimativa, e ausência de livro. Fixtures com o epub real.
- **Override de `kind`** — cada variante falada vence a inferência do LLM.
- **Intervalo de correção** — limitado ao início do capítulo; primeiro recall do livro.
- **Preservação de `## Minhas observações`** ao regenerar, inclusive quando a seção contém
  cabeçalhos que imitam os gerados.
- **Idempotência da migração** — rodar duas vezes não muda nada na segunda.
- **Escrita atômica** dos derivados.

Os testes do bridge são `pytest` e rodam nesta máquina. (Os testes nativos de firmware não
rodam localmente, mas este spec não toca firmware.)

## 13. Decisões deliberadas

Registradas para não serem relidas como descuido:

- **Resumo não usa o texto do livro.** Requisito do usuário. Torna o resumo um espelho do
  entendimento dele, com as lacunas que isso implica.
- **Erros de recall aparecem lado a lado no corpo**, não escondidos em callout recolhido.
  Escolha do usuário contra a recomendação inicial, e coerente com o vault ser material de
  auto-teste.
- **Sem revisão espaçada nova.** O digest semanal que já existe é o canal de retorno.
- **Sem notas atômicas por ideia.** Escrever a partir das notas foi descartado.
- **Ordem de leitura vence ordem cronológica** no nome do arquivo.
- **Resumo do livro atrasa** até a primeira nota do capítulo seguinte.

## 14. Fases de implementação

O desenho é um só, mas não deve virar um plano só. A resolução de capítulo (§6) é
pré-requisito de tudo que vem depois — nome de arquivo, resumo e intervalo de correção
todos dependem dela — e é também a única parte cujo funcionamento não é garantido pelo
desenho, e sim por uma medição contra o epub real. Implementar as duas coisas juntas
significaria descobrir um problema de fundação com features construídas em cima.

**Fase 1 — organização.** Índice de capítulos na conversão, resolução de capítulo, campo
`kind`, layout novo, os três `.base`, migração. No fim da fase 1 o vault já está
organizado e navegável pelos três eixos, sem nenhuma feature nova de LLM.

**Portão entre as fases:** a validação 5/5 da §6. Se a resolução por texto não acertar as
cinco notas reais, a fase 2 não começa — o problema volta para conversa, porque a
alternativa provável (capítulo vindo no metadado do device) muda o escopo para incluir
firmware.

**Fase 2 — síntese.** Correção de recall, resumo de capítulo, resumo do livro.

O benefício prático da separação: a fase 1 entrega sozinha dois dos três usos que o vault
precisa servir — reler na ordem de leitura e caçar o que ficou aberto — e a fase 2
entrega o terceiro. Se a fase 2 atrasar, nada do que a fase 1 fez fica pela metade.


## 15. Decisões revertidas em uso

Registradas com o motivo, porque as duas foram tomadas com argumento e desfeitas com
argumento melhor — vindo de usar o resultado, que é evidência que nenhum desenho no
papel produz.

**Views de Bases → pastas por tipo.** O desenho original punha o eixo de tipo em três
arquivos `.base`, com o argumento de que uma view lê o frontmatter e por isso não pode
dessincronizar. Argumento correto e insuficiente: o pedido era "abrir o Obsidian e **ler**
minhas anotações", e uma Base renderiza uma tabela. Tabela é planilha — linha de metadados
não se lê. `bases.py` foi apagado junto com as views; código que gera arquivo que ninguém
quer é pior que código removido. O que se perdeu foi a visão entre livros, que pasta não
dá: uma pasta é sempre dentro de um livro.

**Nome por posição → nome por data.** `08-012438 Título.md` existia para dar ordem de
leitura numa listagem. Com as notas em três pastas, essa ordem deixou de estar na
listagem e passou a estar no resumo do capítulo — e o nome ficou carregando um código
que não se lê. `2026-09-08 1205 - Título.md` diz algo a quem olha.

**O que sobreviveu às duas reversões:** o `kind` no frontmatter, a resolução de capítulo
pelo trecho, a correção de recall, os dois resumos e a distinção entre fonte e derivado.
Nenhuma das reversões tocou nisso, o que sugere que a fronteira entre *o que o bridge
sabe* e *como o vault mostra* ficou no lugar certo.
