# Organização do vault e fluxo de leitura — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganizar o vault do Obsidian em torno da ordem de leitura, classificar cada nota de voz por intenção, e gerar resumos de capítulo e de livro alimentados só pelo que o usuário registrou.

**Architecture:** Três eixos, três mecanismos, sem duplicar dado — pastas em disco para posição, views de Bases para tipo, quadro do Kanban para status. O capítulo de cada nota é resolvido por busca do trecho no texto convertido, nunca por aritmética de `word_offset`. Resumos são arquivos derivados, reconstruíveis, com uma seção reservada para escrita manual.

**Tech Stack:** Python 3.13, stdlib only (`zipfile`, `ElementTree`, `HTMLParser`, `tomllib`, `json`, `unicodedata`), `pytest` com dublês para Handy e `claude`.

**Spec:** `docs/superpowers/specs/2026-09-09-organizacao-vault-leitura-design.md`

## Global Constraints

- **Nenhuma mudança de firmware.** Só `bridge/` e o vault.
- **Escrita sempre atômica** — arquivo `.partial` + `os.replace`. O vault vive no OneDrive e escrita parcial vira cópia de conflito.
- **Falha nunca custa uma nota.** Qualquer passo depois da escrita da nota é best-effort e só loga `warning`.
- **Nenhuma chamada nova de LLM por nota.** Correção de recall e resumo de capítulo pegam carona nas duas chamadas que já existem. Só o fim de capítulo gasta uma terceira, e é raro.
- **Resumos não usam o texto do livro.** Só texto limpo das notas, pares pergunta/resposta e correções de recall.
- **`## Síntese` tem teto de ~400 palavras** — é a única seção que alimenta o resumo do livro.
- **Testes sem rede, sem hardware, sem tokens.** Handy e `claude` sempre substituídos por dublês.
- **Vault de teste sempre em `tmp_path`.** Nenhum teste toca `C:/Users/kakam/OneDrive/Área de Trabalho/Reading`.
- Valores fixos: `kind` ∈ {`anotação`, `pergunta`, `recall`}; `chapter_source` ∈ {`exato`, `estimado`}; variantes de recall = `recall`, `recal`, `ricol`, `recapitulando`; offset com 6 dígitos.

---

## File Structure

**Novos:**

| Arquivo | Responsabilidade |
|---|---|
| `src/handy_bridge/chapters.py` | Índice de capítulos e resolução do capítulo de uma nota |
| `src/handy_bridge/kind.py` | Política de `kind`: inferência do LLM + override por palavra falada |
| `src/handy_bridge/layout.py` | Todo cálculo de caminho no vault. A §4 do spec vive aqui e em nenhum outro lugar |
| `src/handy_bridge/summaries.py` | Renderização dos resumos e preservação de `## Minhas observações` |
| `src/handy_bridge/bases.py` | Geração dos três arquivos `.base` |
| `src/handy_bridge/migrate.py` | Migração idempotente, com `--dry-run` |

**Modificados:**

| Arquivo | Mudança |
|---|---|
| `src/handy_bridge/epub.py` | Emitir capítulos além do markdown plano; cache do índice em JSON |
| `src/handy_bridge/note.py` | Campos novos no frontmatter; nome de arquivo por posição |
| `src/handy_bridge/postprocess/__init__.py` | `kind` em `PostProcessResult`; `RecallCheck`; três métodos no Protocol |
| `src/handy_bridge/postprocess/claude_cli.py` | Prompts de `kind`, correção de recall, e os dois resumos |
| `src/handy_bridge/pipeline.py` | Fiação de tudo |
| `src/handy_bridge/config.py` | Seção `[layout]` e `[summaries]` |
| `README.md` | Layout novo, `kind`, resumos |

`layout.py` existir separado é deliberado: hoje o caminho do vault é calculado em `note.inbox_path_for`, `kanban.board_path_for` e `epub.ensure_book_markdown` — três lugares com três convenções. O layout novo tem seis caminhos derivados, e espalhá-los repetiria o problema que a §1.5 do spec descreve.

---

# FASE 1 — organização

## Task 1: Extrair capítulos do epub

**Files:**
- Modify: `bridge/src/handy_bridge/epub.py`
- Test: `bridge/tests/test_epub.py`

**Interfaces:**
- Consumes: `_spine_hrefs`, `_ChapterParser` (já existem)
- Produces:
  - `@dataclass(frozen=True) RawChapter: title: str, text: str`
  - `convert_to_chapters(epub_path: Path) -> list[RawChapter]`
  - `convert_to_markdown(epub_path, out_path) -> Path` (inalterado por fora)

Cada entrada do spine é um capítulo. O título sai do primeiro heading markdown do corpo; sem heading, fica `Capítulo N`.

- [ ] **Step 1: Write the failing test**

```python
def test_convert_to_chapters_splits_by_spine_and_reads_titles(tmp_path):
    epub = _make_epub(tmp_path, [
        ("c1.xhtml", "<h1>O Animal Insignificante</h1><p>Primeiro parágrafo.</p>"),
        ("c2.xhtml", "<p>Sem heading nenhum.</p>"),
    ])
    chapters = epub_mod.convert_to_chapters(epub)
    assert [c.title for c in chapters] == ["O Animal Insignificante", "Capítulo 2"]
    assert "Primeiro parágrafo." in chapters[0].text
    # O heading não deve sobrar dentro do texto: ele já é o título.
    assert "O Animal Insignificante" not in chapters[0].text
```

`_make_epub` é um helper novo em `test_epub.py` que monta um zip com `container.xml`, um `.opf` com spine na ordem dada, e os arquivos de corpo. Se `test_epub.py` já tiver um helper equivalente, reusar em vez de duplicar.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bridge && uv run pytest tests/test_epub.py::test_convert_to_chapters_splits_by_spine_and_reads_titles -v`
Expected: FAIL — `AttributeError: module 'handy_bridge.epub' has no attribute 'convert_to_chapters'`

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class RawChapter:
    title: str
    text: str


_HEADING_LINE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$", re.MULTILINE)


def _split_title(text: str, fallback: str) -> tuple[str, str]:
    """Peel the first markdown heading off the body and use it as the title."""
    match = _HEADING_LINE.search(text)
    if match is None or match.start() > 200:
        # A heading far into the chapter is a section break, not the chapter title.
        return fallback, text
    title = match.group("title").strip()
    body = (text[: match.start()] + text[match.end() :]).strip()
    return title or fallback, body


def convert_to_chapters(epub_path: Path) -> list[RawChapter]:
    epub_path = Path(epub_path)
    if not zipfile.is_zipfile(epub_path):
        raise EpubError(f"not a valid epub (not a zip): {epub_path}")

    chapters: list[RawChapter] = []
    with zipfile.ZipFile(epub_path) as archive:
        for href in _spine_hrefs(archive):
            try:
                raw = archive.read(href)
            except KeyError:
                log.warning("spine references a missing file: %s", href)
                continue
            parser = _ChapterParser()
            parser.feed(raw.decode("utf-8", errors="replace"))
            text = parser.text()
            if not text:
                continue
            title, body = _split_title(text, f"Capítulo {len(chapters) + 1}")
            chapters.append(RawChapter(title=title, text=body))
    return chapters
```

E `convert_to_markdown` passa a delegar, para as duas rotas nunca divergirem:

```python
def convert_to_markdown(epub_path: Path, out_path: Path) -> Path:
    out_path = Path(out_path)
    chapters = convert_to_chapters(epub_path)
    chunks = [f"# {c.title}\n\n{c.text}" for c in chapters]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".partial")
    tmp.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return out_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_epub.py -v`
Expected: PASS, incluindo os testes que já existiam.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/epub.py bridge/tests/test_epub.py
git commit -m "feat(bridge): extrair capítulos do epub, não só markdown plano"
```

---

## Task 2: Resolver o capítulo de uma nota

**Files:**
- Create: `bridge/src/handy_bridge/chapters.py`
- Test: `bridge/tests/test_chapters.py`

**Interfaces:**
- Consumes: `epub.convert_to_chapters`, `epub.RawChapter`
- Produces:
  - `@dataclass(frozen=True) Chapter: index: int, title: str, word_count: int, normalized: str`
  - `@dataclass(frozen=True) BookIndex: chapters: list[Chapter]`, `.word_count -> int`, `.width -> int`
  - `@dataclass(frozen=True) Resolution: chapter: int, title: str, source: Literal["exato","estimado"]`
  - `normalize(text: str) -> str`
  - `build_index(epub_path: Path) -> BookIndex`
  - `load_index(vault, book_stem, epub_path) -> BookIndex | None` — cache JSON
  - `resolve(index: BookIndex, excerpt: str | None, word_offset: int | None) -> Resolution | None`

**A decisão de desenho que faz isso funcionar:** o texto é normalizado **por capítulo**, não no livro inteiro. Um casamento dentro do capítulo N devolve N direto, sem precisar mapear posição de caractere normalizado de volta para a original. Isso elimina a classe de bug mais provável aqui.

- [ ] **Step 1: Write the failing tests**

```python
IDX = chapters_mod.BookIndex(chapters=[
    chapters_mod.Chapter(1, "Um", 100, chapters_mod.normalize("o gato subiu no telhado")),
    chapters_mod.Chapter(2, "Dois", 100, chapters_mod.normalize("humanos chegaram a Flores")),
    chapters_mod.Chapter(3, "Três", 100, chapters_mod.normalize("o gato subiu no telhado")),
])


def test_resolve_exact_match_is_exact():
    got = chapters_mod.resolve(IDX, "Humanos chegaram a Flores!", word_offset=None)
    assert (got.chapter, got.source) == (2, "exato")


def test_resolve_ignores_punctuation_case_and_spacing():
    # É a diferença real entre o excerpt do device e o markdown do bridge.
    got = chapters_mod.resolve(IDX, "  HUMANOS   chegaram,\n a Flores  ", word_offset=None)
    assert got.chapter == 2


def test_resolve_ambiguous_uses_word_offset_to_break_the_tie():
    # Capítulos 1 e 3 têm o mesmo texto. Offset 250 cai no capítulo 3.
    got = chapters_mod.resolve(IDX, "o gato subiu no telhado", word_offset=250)
    assert (got.chapter, got.source) == (3, "exato")


def test_resolve_without_match_estimates_from_offset():
    got = chapters_mod.resolve(IDX, "texto que não existe no livro", word_offset=150)
    assert (got.chapter, got.source) == (2, "estimado")


def test_resolve_without_excerpt_or_offset_gives_up():
    assert chapters_mod.resolve(IDX, None, None) is None


def test_resolve_prefers_exact_match_over_offset_disagreement():
    # O trecho manda: offset aproximado não derruba um casamento inequívoco.
    got = chapters_mod.resolve(IDX, "humanos chegaram a Flores", word_offset=9999)
    assert (got.chapter, got.source) == (2, "exato")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_chapters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.chapters'`

- [ ] **Step 3: Write the implementation**

```python
"""Work out which chapter a voice note was recorded in.

The device counts words with its own tokenizer, built from the epub on the board;
the bridge parses the same epub in Python. The two counts do not match, and never
will. So the chapter is resolved by finding the device's excerpt inside the
converted text, and `word_offset` is used only to break ties — the one job an
approximate offset does reliably.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from handy_bridge.epub import EpubError, convert_to_chapters

log = logging.getLogger(__name__)

ChapterSource = Literal["exato", "estimado"]

# Below this many characters an excerpt matches too much to trust.
MIN_EXCERPT_CHARS = 24


def normalize(text: str) -> str:
    """Reduce text to what survives both conversion paths: letters, digits, spaces."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    kept = [
        ch.lower() if (ch.isalnum() or ch.isspace()) else " "
        for ch in decomposed
        if not unicodedata.combining(ch)
    ]
    return " ".join("".join(kept).split())


@dataclass(frozen=True)
class Chapter:
    index: int
    title: str
    word_count: int
    normalized: str


@dataclass(frozen=True)
class BookIndex:
    chapters: list[Chapter]

    @property
    def word_count(self) -> int:
        return sum(c.word_count for c in self.chapters)

    @property
    def width(self) -> int:
        """Digits needed to zero-pad a chapter number so filenames sort right."""
        return max(2, len(str(len(self.chapters))))

    def start_of(self, chapter: int) -> int:
        """Word offset where a chapter begins, in the bridge's own counting."""
        return sum(c.word_count for c in self.chapters if c.index < chapter)

    def get(self, chapter: int) -> Chapter | None:
        return next((c for c in self.chapters if c.index == chapter), None)


@dataclass(frozen=True)
class Resolution:
    chapter: int
    title: str
    source: ChapterSource


def build_index(epub_path: Path) -> BookIndex:
    chapters = []
    for position, raw in enumerate(convert_to_chapters(epub_path), start=1):
        normalized = normalize(raw.text)
        chapters.append(
            Chapter(
                index=position,
                title=raw.title,
                word_count=len(normalized.split()),
                normalized=normalized,
            )
        )
    return BookIndex(chapters=chapters)


def _estimate(index: BookIndex, word_offset: int) -> Resolution | None:
    """Map an offset onto a chapter by scaling against the bridge's word count.

    The device's total and the bridge's total differ, so this uses the *fraction*
    of the book rather than the raw offset — a proportion survives a tokenizer
    mismatch that an absolute count does not.
    """
    if not index.chapters or index.word_count <= 0:
        return None
    running = 0
    for chapter in index.chapters:
        running += chapter.word_count
        if word_offset < running:
            return Resolution(chapter.index, chapter.title, "estimado")
    last = index.chapters[-1]
    return Resolution(last.index, last.title, "estimado")


def resolve(
    index: BookIndex, excerpt: str | None, word_offset: int | None
) -> Resolution | None:
    """Find the chapter, preferring the excerpt and falling back to the offset."""
    needle = normalize(excerpt or "")
    if len(needle) >= MIN_EXCERPT_CHARS:
        hits = [c for c in index.chapters if needle in c.normalized]
        if len(hits) == 1:
            return Resolution(hits[0].index, hits[0].title, "exato")
        if len(hits) > 1:
            # Repeated passage: take the occurrence nearest the reported position.
            if word_offset is None:
                chosen = hits[0]
            else:
                chosen = min(
                    hits, key=lambda c: abs(index.start_of(c.index) - word_offset)
                )
            return Resolution(chosen.index, chosen.title, "exato")

    if word_offset is not None:
        return _estimate(index, int(word_offset))
    return None
```

E o cache, que segue o padrão de "converter uma vez" que `ensure_book_markdown` já usa:

```python
def index_cache_path(source_dir: Path, book_stem: str) -> Path:
    return Path(source_dir) / f"{book_stem}.chapters.json"


def load_index(source_dir: Path, book_stem: str, epub_path: Path) -> BookIndex | None:
    """Build the index once per book and cache it next to the converted text."""
    cache = index_cache_path(source_dir, book_stem)
    if cache.is_file():
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
            return BookIndex(chapters=[Chapter(**c) for c in raw["chapters"]])
        except (ValueError, KeyError, TypeError) as exc:
            log.warning("chapter cache for %r is unusable, rebuilding: %s", book_stem, exc)

    if not Path(epub_path).is_file():
        log.info("no epub for %r, cannot index chapters", book_stem)
        return None
    try:
        index = build_index(epub_path)
    except (EpubError, OSError) as exc:
        log.warning("could not index chapters for %r: %s", book_stem, exc)
        return None

    cache.parent.mkdir(parents=True, exist_ok=True)
    payload = {"chapters": [c.__dict__ for c in index.chapters]}
    tmp = cache.with_name(cache.name + ".partial")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(cache)
    return index
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_chapters.py -v`
Expected: PASS, 6 testes.

- [ ] **Step 5: Add a cache test and re-run**

```python
def test_load_index_caches_and_survives_a_corrupt_cache(tmp_path, monkeypatch):
    epub = _make_epub(tmp_path, [("c1.xhtml", "<h1>Um</h1><p>texto do capitulo um</p>")])
    src = tmp_path / "fonte"
    first = chapters_mod.load_index(src, "livro", epub)
    assert first is not None and chapters_mod.index_cache_path(src, "livro").is_file()

    # Um cache corrompido reconstrói em vez de explodir.
    chapters_mod.index_cache_path(src, "livro").write_text("{lixo", encoding="utf-8")
    again = chapters_mod.load_index(src, "livro", epub)
    assert again is not None and again.chapters[0].title == "Um"


def test_load_index_returns_none_without_an_epub(tmp_path):
    assert chapters_mod.load_index(tmp_path, "sumido", tmp_path / "sumido.epub") is None
```

Run: `cd bridge && uv run pytest tests/test_chapters.py -v`
Expected: PASS, 8 testes.

- [ ] **Step 6: Commit**

```bash
git add bridge/src/handy_bridge/chapters.py bridge/tests/test_chapters.py
git commit -m "feat(bridge): resolver capítulo pelo trecho, com offset só como desempate"
```

---

## Task 3: PORTÃO — validar a resolução contra as 5 notas reais

**Files:**
- Create: `bridge/tools/check_chapters.py`

**Interfaces:**
- Consumes: `chapters.load_index`, `chapters.resolve`

Esta tarefa não entrega feature. Ela decide se a Fase 2 acontece. A §6 do spec exige 5/5 de casamento **exato** contra as notas que já existem no vault; abaixo disso, a fundação não sustenta o resto e o desenho volta para conversa.

O script lê o trecho de cada nota a partir do callout `> [!quote]`, que é onde o texto do device foi preservado.

- [ ] **Step 1: Write the script**

```python
"""Report how the chapter resolver does against the notes already in the vault.

Run before building anything on top of chapter resolution:
    uv run python tools/check_chapters.py "<vault>" "<book stem>"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from handy_bridge import chapters as chapters_mod  # noqa: E402

QUOTE = "> [!quote]"
OFFSET = re.compile(r"^word_offset:\s*(\d+)\s*$", re.MULTILINE)


def read_excerpt(text: str) -> str | None:
    lines = text.splitlines()
    try:
        at = next(i for i, line in enumerate(lines) if line.startswith(QUOTE))
    except StopIteration:
        return None
    out = []
    for line in lines[at + 1 :]:
        if not line.startswith(">"):
            break
        out.append(line.lstrip("> ").rstrip())
    return " ".join(out).strip() or None


def main() -> int:
    vault, stem = Path(sys.argv[1]), sys.argv[2]
    index = chapters_mod.load_index(vault / "fonte", stem, vault / f"{stem}.epub")
    if index is None:
        index = chapters_mod.load_index(vault / "fonte", stem, vault / "fonte" / f"{stem}.epub")
    if index is None:
        print(f"FALHA: não consegui indexar {stem}")
        return 2
    print(f"{len(index.chapters)} capítulos, {index.word_count} palavras\n")

    notes = sorted((vault / "Inbox" / stem).glob("*.md"))
    if not notes:
        notes = sorted((vault / "Livros" / stem / "Notas").glob("*.md"))
    exact = 0
    for note in notes:
        text = note.read_text(encoding="utf-8")
        excerpt = read_excerpt(text)
        match = OFFSET.search(text)
        offset = int(match.group(1)) if match else None
        got = chapters_mod.resolve(index, excerpt, offset)
        if got is None:
            verdict = "SEM RESOLUÇÃO"
        else:
            verdict = f"cap {got.chapter} ({got.title}) [{got.source}]"
            exact += got.source == "exato"
        print(f"  {note.name[:58]:58} offset={offset!s:>7}  {verdict}")

    print(f"\nexatos: {exact}/{len(notes)}")
    return 0 if notes and exact == len(notes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it against the real vault**

Run:
```bash
cd bridge && uv run python tools/check_chapters.py \
  "C:/Users/kakam/OneDrive/Área de Trabalho/Reading" \
  "epdf.pub_sapiens-uma-breve-historia-da-humanidade"
```
Expected: `exatos: 5/5`, e cada nota com um capítulo plausível.

- [x] **Step 3: Decide** — **PORTÃO FECHADO EM 5/5, Fase 2 liberada.**

Resultado da primeira execução: **4/5**. A nota de 2026-09-09 22:05 falhou porque o
trecho enviado pelo device traz `passaram porum processo` onde o livro diz `por um` —
palavras soldadas pela conversão HTML-para-texto do device, não erro de ASR. A nota
resolvia no capítulo certo, mas por estimativa.

Corrigido em `chapters.compact` / `chapters._match`: o casamento tenta primeiro com
espaços e, sem acerto, tenta sem espaço nenhum, o que torna artefato de junção de
palavra invisível. Estrito antes de frouxo, para não perder precisão em texto limpo.
Regressão coberta por `test_a_word_join_artefact_from_the_device_still_matches`, com o
texto real que falhou. Segunda execução: **5/5**.

**Achado cosmético para o usuário decidir:** a numeração sai do spine do epub, que conta
capa, folha de rosto e sumário. O capítulo 1 do Sapiens vira `03`. Ordenação e título
ficam corretos; só o número não corresponde ao do livro. Não renumerado de propósito —
heurística de front matter erra em silêncio.

- **5/5** → seguir para a Task 4.
- **Menos que 5/5** → **PARAR.** Registrar no plano qual nota falhou e por quê (trecho ausente, trecho reescrito pelo device, normalização insuficiente). A saída provável é o device passar o capítulo no metadado, que é firmware e está fora deste plano. Não construir resumo sobre capítulo estimado.

- [ ] **Step 4: Commit**

```bash
git add bridge/tools/check_chapters.py
git commit -m "tools: medir a resolução de capítulo contra as notas reais"
```

---

## Task 4: Classificar a nota por intenção

**Files:**
- Create: `bridge/src/handy_bridge/kind.py`
- Modify: `bridge/src/handy_bridge/postprocess/__init__.py`
- Modify: `bridge/src/handy_bridge/postprocess/claude_cli.py`
- Test: `bridge/tests/test_kind.py`, `bridge/tests/test_postprocess.py`

**Interfaces:**
- Produces:
  - `NoteKind = Literal["anotação", "pergunta", "recall"]`
  - `RECALL_MARKERS: tuple[str, ...]`
  - `kind.resolve_kind(raw_transcript: str, inferred: str | None) -> NoteKind`
  - `PostProcessResult.kind: NoteKind = "anotação"` (campo novo, com default)

Duas camadas, como o spec decidiu: o LLM infere de graça no JSON que já volta, e a palavra falada vence por comparação em código. As variantes fonéticas existem porque o Nemotron transcreve português e "recall" é palavra inglesa no meio da fala.

- [ ] **Step 1: Write the failing tests**

```python
def test_marker_word_overrides_the_model():
    got = kind_mod.resolve_kind("recapitulando, o big bang foi...", inferred="pergunta")
    assert got == "recall"


@pytest.mark.parametrize("spoken", ["recall", "recal", "ricol", "recapitulando"])
def test_every_phonetic_variant_triggers_the_override(spoken):
    assert kind_mod.resolve_kind(f"então {spoken}: o que eu entendi foi", None) == "recall"


def test_marker_must_be_a_whole_word():
    # "recalcular" contém "recal" e não pode disparar o override.
    assert kind_mod.resolve_kind("preciso recalcular a média", "anotação") == "anotação"


def test_falls_back_to_the_model_when_no_marker_is_spoken():
    assert kind_mod.resolve_kind("o que é entropia?", "pergunta") == "pergunta"


def test_unknown_model_value_degrades_to_annotation():
    # Um valor inventado pelo LLM nunca deve virar kind inválido no frontmatter.
    assert kind_mod.resolve_kind("texto", "reflexão") == "anotação"
    assert kind_mod.resolve_kind("texto", None) == "anotação"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_kind.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.kind'`

- [ ] **Step 3: Write the implementation**

```python
"""Decide what a recording was for: a note, a question, or a memory check.

Two layers on purpose. The model infers the kind inside the JSON it already
returns, which costs nothing; a spoken marker word then overrides it in code.
The model tolerates phrasing the regex never anticipated, and the regex gives the
determinism the marker word is there to provide.
"""

from __future__ import annotations

import re
from typing import Literal

NoteKind = Literal["anotação", "pergunta", "recall"]

VALID_KINDS: tuple[NoteKind, ...] = ("anotação", "pergunta", "recall")
DEFAULT_KIND: NoteKind = "anotação"

# "recall" is an English word spoken mid-Portuguese, so the Nemotron may render it
# several ways. Matching is plain text, so accepting the variants costs nothing and
# stops the override from failing on an accent.
RECALL_MARKERS: tuple[str, ...] = ("recall", "recal", "ricol", "recapitulando")

_MARKER_RE = re.compile(
    r"(?<![0-9a-zà-ÿ])(?:" + "|".join(RECALL_MARKERS) + r")(?![0-9a-zà-ÿ])",
    re.IGNORECASE,
)


def spoken_recall_marker(transcript: str) -> bool:
    """True when the speaker said one of the marker words as a whole word."""
    return bool(_MARKER_RE.search(transcript or ""))


def resolve_kind(raw_transcript: str, inferred: str | None) -> NoteKind:
    if spoken_recall_marker(raw_transcript):
        return "recall"
    if inferred in VALID_KINDS:
        return inferred  # type: ignore[return-value]
    return DEFAULT_KIND
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_kind.py -v`
Expected: PASS, 8 testes (a parametrização conta 4).

- [ ] **Step 5: Add `kind` to the post-process result and prompt**

Em `postprocess/__init__.py`, acrescentar o campo com default para que backends antigos e testes existentes sigam válidos:

```python
from handy_bridge.kind import DEFAULT_KIND, NoteKind

@dataclass(frozen=True)
class PostProcessResult:
    title: str
    tags: list[str]
    cleaned: str
    tasks: list[Task] = field(default_factory=list)
    kind: NoteKind = DEFAULT_KIND
```

Em `claude_cli.py`, estender o `PROMPT` — no mesmo objeto JSON, sem chamada nova:

```python
    '{"title": string, "tags": array de strings, "cleaned": string, '
    '"kind": "anotação" | "pergunta" | "recall", '
    '"tasks": array de {"text": string, "source": "keyword" ou "inferred", '
    '"answerable": boolean}}.\n'
```

e a explicação do campo:

```python
    '"kind" é a intenção da gravação. Use "recall" quando a pessoa está afirmando o '
    "que entendeu ou lembrou, para conferir se acertou. Use \"pergunta\" quando ela "
    "está pedindo explicação de algo que não entendeu. Use \"anotação\" para "
    "comentário, opinião ou registro solto. Em dúvida entre recall e anotação, "
    'escolha "anotação": afirmar entendimento é diferente de comentar.\n'
```

E no parsing, `kind` passa pelo `resolve_kind` para que o override valha sempre:

```python
        kind=resolve_kind(transcript, str(parsed.get("kind", "")) or None),
```

- [ ] **Step 6: Test the wiring and re-run everything**

```python
def test_process_applies_the_spoken_override_over_the_model(monkeypatch):
    fake = _fake_claude(monkeypatch, {
        "title": "t", "tags": [], "cleaned": "c", "kind": "pergunta", "tasks": [],
    })
    got = ClaudeCliProcessor("m").process("recapitulando: o big bang foi uma expansão")
    assert got.kind == "recall"
```

`_fake_claude` é o dublê que `test_postprocess.py` já usa; reusar em vez de criar outro.

Run: `cd bridge && uv run pytest -v`
Expected: PASS, tudo verde.

- [ ] **Step 7: Commit**

```bash
git add bridge/src/handy_bridge/kind.py bridge/src/handy_bridge/postprocess/ bridge/tests/test_kind.py bridge/tests/test_postprocess.py
git commit -m "feat(bridge): classificar nota como anotação, pergunta ou recall"
```

---

## Task 5: Centralizar os caminhos do vault

**Files:**
- Create: `bridge/src/handy_bridge/layout.py`
- Test: `bridge/tests/test_layout.py`

**Interfaces:**
- Consumes: `note.slugify`
- Produces:
  - `BOOKS_DIR = "Livros"`, `GENERAL_DIR = "Geral"`, `NOTES_DIR = "Notas"`, `CHAPTERS_DIR = "Capítulos"`, `SOURCE_DIR = "fonte"`, `BOARD_FILE = "Quadro.md"`
  - `safe_stem(book_stem: str | None) -> str`
  - `book_dir(vault, book_stem) -> Path`
  - `notes_dir(vault, book_stem) -> Path`
  - `chapters_dir(vault, book_stem) -> Path`
  - `source_dir(vault, book_stem) -> Path`
  - `board_path(vault, book_stem) -> Path`
  - `book_summary_path(vault, book_stem) -> Path`
  - `chapter_summary_path(vault, book_stem, chapter, title, width) -> Path`
  - `note_stem(chapter, word_offset, title, width) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def test_note_stem_sorts_by_reading_position(tmp_path):
    a = layout.note_stem(4, 12438, "Crítica à tese", width=2)
    b = layout.note_stem(10, 300, "Outra coisa", width=2)
    assert a == "04-012438 Crítica à tese"
    # Ordem de leitura, não alfabética nem cronológica.
    assert sorted([b, a]) == [a, b]


def test_note_stem_handles_a_missing_offset():
    assert layout.note_stem(4, None, "Sem offset", width=2) == "04-000000 Sem offset"


def test_safe_stem_refuses_to_escape_the_vault():
    # O stem vem do device, então não pode virar caminho para fora.
    assert layout.safe_stem("../../etc/passwd") == "passwd"
    assert layout.safe_stem("a/b/livro") == "livro"
    assert layout.safe_stem("") == layout.GENERAL_DIR
    assert layout.safe_stem("..") == layout.GENERAL_DIR
    assert layout.safe_stem(None) == layout.GENERAL_DIR


def test_book_paths_all_live_under_one_directory(tmp_path):
    v = tmp_path
    assert layout.notes_dir(v, "Sapiens") == v / "Livros/Sapiens/Notas"
    assert layout.chapters_dir(v, "Sapiens") == v / "Livros/Sapiens/Capítulos"
    assert layout.board_path(v, "Sapiens") == v / "Livros/Sapiens/Quadro.md"
    assert layout.book_summary_path(v, "Sapiens") == v / "Livros/Sapiens/Sapiens.md"
    assert layout.source_dir(v, "Sapiens") == v / "Livros/Sapiens/fonte"


def test_notes_without_a_book_go_to_geral(tmp_path):
    assert layout.notes_dir(tmp_path, None) == tmp_path / "Geral/Notas"
    assert layout.board_path(tmp_path, None) == tmp_path / "Geral/Quadro.md"


def test_chapter_summary_path_is_numbered_and_titled(tmp_path):
    got = layout.chapter_summary_path(tmp_path, "Sapiens", 4, "Os Navegadores", width=2)
    assert got == tmp_path / "Livros/Sapiens/Capítulos/04 - Os Navegadores.md"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_layout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.layout'`

- [ ] **Step 3: Write the implementation**

```python
"""Every path inside the vault, in one place.

The layout used to be computed in three modules with three conventions, which is
how a single book ended up living in four directories. Six derived paths spread
the same way would repeat that, so they all live here.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from handy_bridge.note import slugify

BOOKS_DIR = "Livros"
GENERAL_DIR = "Geral"
NOTES_DIR = "Notas"
CHAPTERS_DIR = "Capítulos"
SOURCE_DIR = "fonte"
BOARD_FILE = "Quadro.md"

OFFSET_DIGITS = 6


def safe_stem(book_stem: str | None) -> str:
    """Reduce a device-supplied stem to a single safe directory name."""
    name = (book_stem or "").strip()
    name = PurePosixPath(name.replace("\\", "/")).name if name else ""
    if not name or name in {".", ".."}:
        return GENERAL_DIR
    return name


def book_dir(vault_path: Path, book_stem: str | None) -> Path:
    stem = safe_stem(book_stem)
    if stem == GENERAL_DIR:
        return Path(vault_path) / GENERAL_DIR
    return Path(vault_path) / BOOKS_DIR / stem


def notes_dir(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / NOTES_DIR


def chapters_dir(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / CHAPTERS_DIR


def source_dir(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / SOURCE_DIR


def board_path(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / BOARD_FILE


def book_summary_path(vault_path: Path, book_stem: str | None) -> Path:
    stem = safe_stem(book_stem)
    return book_dir(vault_path, book_stem) / f"{slugify(stem)}.md"


def note_stem(chapter: int | None, word_offset: int | None, title: str, width: int) -> str:
    """Name a note by where it sits in the book, not when it was recorded.

    Reading order is not recording order: re-reading chapter 2 after chapter 8 must
    put the note back at chapter 2. The date stays in the frontmatter.
    """
    number = str(chapter if chapter is not None else 0).zfill(max(2, width))
    offset = str(word_offset if word_offset is not None else 0).zfill(OFFSET_DIGITS)
    return f"{number}-{offset} {slugify(title)}"


def chapter_summary_path(
    vault_path: Path, book_stem: str | None, chapter: int, title: str, width: int
) -> Path:
    number = str(chapter).zfill(max(2, width))
    return chapters_dir(vault_path, book_stem) / f"{number} - {slugify(title)}.md"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_layout.py -v`
Expected: PASS, 6 testes.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/layout.py bridge/tests/test_layout.py
git commit -m "feat(bridge): centralizar o layout do vault num módulo só"
```

---

## Task 6: Escrever a nota no layout novo

**Files:**
- Modify: `bridge/src/handy_bridge/note.py`
- Test: `bridge/tests/test_note.py`

**Interfaces:**
- Consumes: `layout.note_stem`, `kind.NoteKind`
- Produces:
  - `NoteData` com `kind`, `chapter`, `chapter_title`, `chapter_source`
  - `write_note(notes_dir: Path, note: NoteData, *, width: int = 2) -> Path`
  - `inbox_path_for` **removido** — substituído por `layout.notes_dir`

Ordem dos campos no frontmatter importa: `kind` logo depois de `title` para ficar visível no painel de propriedades sem rolar.

- [ ] **Step 1: Write the failing tests**

```python
def test_frontmatter_carries_kind_and_chapter():
    note = _note(kind="recall", chapter=4, chapter_title="Os Navegadores",
                 chapter_source="exato", book="Sapiens", word_offset=12438)
    out = note_mod.render(note)
    assert "kind: recall" in out
    assert "chapter: 4" in out
    assert 'chapter_title: "Os Navegadores"' in out
    assert "chapter_source: exato" in out


def test_chapter_fields_are_omitted_when_there_is_no_book():
    out = note_mod.render(_note(book=None, chapter=None))
    assert "chapter:" not in out
    assert "chapter_title:" not in out
    # kind sempre aparece: é o eixo das views e não pode faltar.
    assert "kind: anotação" in out


def test_write_note_names_the_file_by_reading_position(tmp_path):
    path = note_mod.write_note(
        tmp_path, _note(title="Física e química", chapter=1, word_offset=1324), width=2
    )
    assert path.name == "01-001324 Física e química.md"


def test_write_note_never_overwrites_a_same_named_note(tmp_path):
    first = note_mod.write_note(tmp_path, _note(title="Igual", chapter=1, word_offset=5))
    second = note_mod.write_note(tmp_path, _note(title="Igual", chapter=1, word_offset=5))
    assert first != second and second.name.endswith("-2.md")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_note.py -v`
Expected: FAIL — `TypeError: NoteData.__init__() got an unexpected keyword argument 'kind'`

- [ ] **Step 3: Extend `NoteData` and `render`**

```python
    kind: NoteKind = DEFAULT_KIND
    # Resolved from the excerpt, so present only for notes born inside the reader.
    chapter: int | None = None
    chapter_title: str | None = None
    chapter_source: str | None = None
```

No `render`, depois da linha de `title`:

```python
        f"kind: {note.kind}",
```

e junto dos campos de âncora, depois de `word_offset`:

```python
    if note.chapter is not None:
        lines.append(f"chapter: {note.chapter}")
        if note.chapter_title:
            lines.append(f"chapter_title: {_quote(note.chapter_title)}")
        if note.chapter_source:
            lines.append(f"chapter_source: {note.chapter_source}")
```

- [ ] **Step 4: Replace the filename logic and drop `inbox_path_for`**

```python
def write_note(notes_dir: Path, note: NoteData, *, width: int = 2) -> Path:
    """Write the note atomically so OneDrive never syncs a partial file."""
    notes_dir = Path(notes_dir)
    notes_dir.mkdir(parents=True, exist_ok=True)
    from handy_bridge.layout import note_stem  # local: layout imports slugify from here

    target = _unique_path(notes_dir, note_stem(note.chapter, note.word_offset, note.title, width))
    tmp = target.with_name(target.name + ".partial")
    tmp.write_text(render(note), encoding="utf-8")
    os.replace(tmp, target)
    return target
```

O import local é o preço de `layout` depender de `note.slugify`. Alternativa considerada e rejeitada: mover `slugify` para um terceiro módulo só para desfazer o ciclo, o que criaria um arquivo com uma função.

Remover `inbox_path_for` e `GENERAL_FOLDER`, e os testes que os cobriam — `layout.safe_stem` já cobre o caso de travessia de caminho, com os mesmos casos.

- [ ] **Step 5: Run the whole suite**

Run: `cd bridge && uv run pytest -v`
Expected: FAIL apenas em `test_pipeline.py`, que ainda chama `inbox_path_for`. Corrigir na Task 7.

- [ ] **Step 6: Commit**

```bash
git add bridge/src/handy_bridge/note.py bridge/tests/test_note.py
git commit -m "feat(bridge): nota com kind e capítulo, nomeada por posição de leitura"
```

---

## Task 7: Fiar a Fase 1 no pipeline

**Files:**
- Modify: `bridge/src/handy_bridge/pipeline.py`
- Modify: `bridge/src/handy_bridge/epub.py` (`ensure_book_markdown` aponta para `fonte/`)
- Modify: `bridge/src/handy_bridge/kanban.py` (`board_path_for` delega a `layout`)
- Test: `bridge/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `layout.*`, `chapters.load_index`, `chapters.resolve`
- Produces: `process_note` gravando no layout novo, com `kind` e capítulo resolvidos

- [ ] **Step 1: Write the failing test**

```python
def test_process_note_writes_into_the_book_layout_with_a_resolved_chapter(tmp_path):
    vault = _vault_with_epub(tmp_path, chapters=[
        ("Um", "o comeco do livro fala de coisas"),
        ("Dois", "humanos chegaram a ilha de Flores quando o nivel do mar estava baixo"),
    ])
    incoming = _incoming(meta={
        "clock_synced": True, "recorded_at": "2026-09-09T22:05:40",
        "book": "livro", "word_offset": 1324,
        "excerpt": "Humanos chegaram à ilha de Flores, quando o nível do mar estava baixo.",
    })
    path = process_note(incoming, _cfg(vault), transcribe_fn=_fake_transcribe,
                        processor=_fake_processor(kind="recall"))

    assert path.parent == vault / "Livros/livro/Notas"
    assert path.name.startswith("02-001324 ")
    text = path.read_text(encoding="utf-8")
    assert "kind: recall" in text and "chapter: 2" in text
    assert "chapter_source: exato" in text


def test_process_note_without_a_book_lands_in_geral(tmp_path):
    path = process_note(_incoming(meta={"clock_synced": True}), _cfg(tmp_path),
                        transcribe_fn=_fake_transcribe, processor=_fake_processor())
    assert path.parent == tmp_path / "Geral/Notas"
    assert "chapter:" not in path.read_text(encoding="utf-8")


def test_a_broken_epub_still_produces_a_note(tmp_path):
    # Regra global: falha de conveniência nunca custa uma nota.
    vault = tmp_path
    (vault / "Livros/livro/fonte").mkdir(parents=True)
    (vault / "Livros/livro/fonte/livro.epub").write_bytes(b"nao sou um zip")
    path = process_note(
        _incoming(meta={"clock_synced": True, "book": "livro", "excerpt": "qualquer"}),
        _cfg(vault), transcribe_fn=_fake_transcribe, processor=_fake_processor())
    assert path.is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'inbox_path_for'`

- [ ] **Step 3: Resolve the chapter in `process_note`**

Substituir o bloco `book`/`word_offset`/`write_note` por:

```python
    book = incoming.meta.get("book") or None
    raw_offset = incoming.meta.get("word_offset")
    word_offset = int(raw_offset) if raw_offset is not None else None

    chapter = None
    width = 2
    if book:
        try:
            ensure_book_markdown(cfg.vault_path, book)
        except (EpubError, OSError) as exc:
            log.warning("could not convert book %r: %s", book, exc)
        index = chapters.load_index(
            layout.source_dir(cfg.vault_path, book), book, _epub_source(cfg.vault_path, book)
        )
        if index is not None:
            width = index.width
            chapter = chapters.resolve(index, excerpt, word_offset)

    note_path = write_note(
        layout.notes_dir(cfg.vault_path, book),
        NoteData(
            ...,
            kind=result_kind,
            chapter=chapter.chapter if chapter else None,
            chapter_title=chapter.title if chapter else None,
            chapter_source=chapter.source if chapter else None,
        ),
        width=width,
    )
```

`result_kind` vem do `PostProcessResult`; sem processor, cai no default via `kind.resolve_kind(raw_text, None)`, para que o override por palavra falada funcione mesmo com `post_process.enabled = false`.

`_epub_source` procura o epub em `fonte/` primeiro e na raiz do vault depois, para que um vault ainda não migrado continue funcionando:

```python
def _epub_source(vault_path: Path, book: str) -> Path:
    in_source = layout.source_dir(vault_path, book) / f"{book}.epub"
    return in_source if in_source.is_file() else Path(vault_path) / f"{book}.epub"
```

- [ ] **Step 4: Point `ensure_book_markdown` and `board_path_for` at the new layout**

`ensure_book_markdown(vault_path, book_stem)` passa a escrever em `layout.source_dir(vault, stem)/f"{stem}.md"` e a procurar o epub via `_epub_source`. O parâmetro `subfolder="Books"` sai.

`kanban.board_path_for(vault_path, subfolder, book_stem)` passa a delegar:

```python
def board_path_for(vault_path: Path, subfolder: str, book_stem: str | None) -> Path:
    # `subfolder` is kept for config compatibility and deliberately unused: the board
    # now lives inside the book's own directory.
    del subfolder
    return layout.board_path(vault_path, book_stem)
```

- [ ] **Step 5: Run the whole suite**

Run: `cd bridge && uv run pytest -v`
Expected: PASS. Ajustar `test_kanban.py` e `test_epub.py` para os caminhos novos.

- [ ] **Step 6: Commit**

```bash
git add bridge/src/handy_bridge/pipeline.py bridge/src/handy_bridge/epub.py bridge/src/handy_bridge/kanban.py bridge/tests/
git commit -m "feat(bridge): gravar nota, quadro e fonte no diretório do livro"
```

---

## Task 8: Gerar as views de Bases

**Files:**
- Create: `bridge/src/handy_bridge/bases.py`
- Test: `bridge/tests/test_bases.py`

**Interfaces:**
- Produces: `write_bases(vault_path: Path) -> list[Path]`

**Risco conhecido, e o que fazer com ele.** O `.base` é o único artefato deste plano que não dá para verificar sem abrir o Obsidian. O único `.base` real na máquina (`plum-helisul/Sem título.base`) confirma o container — YAML com `views:`, cada view com `type` e `name` — mas está vazio e não revela a sintaxe de filtro. A mitigação é estrutural, não uma aposta: **se uma chave de filtro estiver errada, a view mostra tudo em vez de quebrar**, e o `kind` no frontmatter continua encontrável pela busca nativa (`kind: recall`), que o usuário pode salvar nos Bookmarks. Nenhum caminho de leitura depende do filtro estar certo.

- [ ] **Step 1: Write the failing test**

```python
def test_write_bases_creates_one_file_per_kind(tmp_path):
    written = bases.write_bases(tmp_path)
    assert {p.name for p in written} == {"Anotações.base", "Perguntas.base", "Recall.base"}
    for path in written:
        assert path.is_file()


def test_each_base_is_valid_yaml_with_a_table_view(tmp_path):
    # A sintaxe de filtro não é verificável aqui, mas YAML quebrado é.
    import yaml  # dev-only dependency; see Step 3
    for path in bases.write_bases(tmp_path):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert parsed["views"][0]["type"] == "table"


def test_write_bases_is_idempotent(tmp_path):
    first = [p.read_text(encoding="utf-8") for p in bases.write_bases(tmp_path)]
    second = [p.read_text(encoding="utf-8") for p in bases.write_bases(tmp_path)]
    assert first == second
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_bases.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.bases'`

- [ ] **Step 3: Add `pyyaml` as a dev dependency and implement**

```bash
cd bridge && uv add --dev pyyaml
```

`pyyaml` entra só como dependência de teste: o módulo escreve YAML à mão, porque o formato é fixo e curto, e uma dependência de runtime nova para três arquivos estáticos não se paga.

```python
"""Write the Bases views that make each kind of note one click away.

The three axes of the vault are split across three mechanisms: directories carry
reading position, the Kanban board carries status, and these views carry kind. A
view reads the notes' frontmatter, so unlike a generated index it cannot fall out
of sync.
"""

from __future__ import annotations

from pathlib import Path

from handy_bridge.layout import BOOKS_DIR

# (filename, kind value, view label)
VIEWS = (
    ("Anotações.base", "anotação", "Anotações"),
    ("Perguntas.base", "pergunta", "Perguntas"),
    ("Recall.base", "recall", "Recall"),
)

_TEMPLATE = """filters:
  and:
    - 'kind == "{kind}"'
    - 'file.inFolder("{books}")'
views:
  - type: table
    name: {label}
    order:
      - book
      - chapter
      - title
      - date
      - tags
    sort:
      - property: book
        direction: ASC
      - property: chapter
        direction: ASC
      - property: word_offset
        direction: ASC
"""


def render(kind: str, label: str) -> str:
    return _TEMPLATE.format(kind=kind, label=label, books=BOOKS_DIR)


def write_bases(vault_path: Path) -> list[Path]:
    """Create the three entry points. Rewriting them is safe: they are derived."""
    vault_path = Path(vault_path)
    vault_path.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, kind, label in VIEWS:
        target = vault_path / filename
        tmp = target.with_name(target.name + ".partial")
        tmp.write_text(render(kind, label), encoding="utf-8")
        tmp.replace(target)
        written.append(target)
    return written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_bases.py -v`
Expected: PASS, 3 testes.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/bases.py bridge/tests/test_bases.py bridge/pyproject.toml bridge/uv.lock
git commit -m "feat(bridge): views de Bases por tipo de nota"
```

---

## Task 9: Migrar o vault existente

**Files:**
- Create: `bridge/src/handy_bridge/migrate.py`
- Test: `bridge/tests/test_migrate.py`

**Interfaces:**
- Consumes: `layout.*`, `chapters.*`, `bases.write_bases`, `kind.resolve_kind`
- Produces:
  - `@dataclass Move: src: Path, dst: Path, why: str`
  - `plan_migration(vault_path: Path) -> list[Move]`
  - `apply_migration(vault_path: Path, *, dry_run: bool) -> list[Move]`
  - CLI: `python -m handy_bridge.migrate --vault <path> [--apply]`

`--dry-run` é o padrão; mover 4,7 MB de epub dentro do OneDrive exige ver o plano antes.

- [ ] **Step 1: Write the failing tests**

```python
def test_migration_moves_notes_into_the_book_layout(tmp_path):
    vault = _legacy_vault(tmp_path)   # Inbox/<livro>/*.md, Quadros/<livro>.md, Books/, *.epub
    migrate.apply_migration(vault, dry_run=False)
    notes = list((vault / "Livros/livro/Notas").glob("*.md"))
    assert notes and all(n.name[:2].isdigit() for n in notes)
    assert (vault / "Livros/livro/Quadro.md").is_file()
    assert (vault / "Livros/livro/fonte/livro.epub").is_file()


def test_migration_dedupes_identical_cards_keeping_the_checked_one(tmp_path):
    vault = _legacy_vault(tmp_path, board_lanes={"Concluído": [
        "- [ ] Entender o Big Bang [[nota-a]]",
        "- [x] Entender o Big Bang [[nota-b]]",
    ]})
    migrate.apply_migration(vault, dry_run=False)
    board = (vault / "Livros/livro/Quadro.md").read_text(encoding="utf-8")
    assert board.count("Entender o Big Bang") == 1
    assert "- [x] Entender o Big Bang" in board


def test_dry_run_changes_nothing_on_disk(tmp_path):
    vault = _legacy_vault(tmp_path)
    before = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
    moves = migrate.apply_migration(vault, dry_run=True)
    after = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
    assert moves and before == after


def test_migration_is_idempotent(tmp_path):
    vault = _legacy_vault(tmp_path)
    migrate.apply_migration(vault, dry_run=False)
    snapshot = sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*"))
    assert migrate.apply_migration(vault, dry_run=False) == []
    assert sorted(p.relative_to(vault).as_posix() for p in vault.rglob("*")) == snapshot


def test_migration_keeps_a_directory_it_could_not_classify(tmp_path):
    vault = _legacy_vault(tmp_path)
    (vault / "Inbox" / "solto.txt").write_text("nao sei o que e isso", encoding="utf-8")
    migrate.apply_migration(vault, dry_run=False)
    # Migração não apaga o que não entendeu.
    assert (vault / "Inbox" / "solto.txt").is_file()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bridge && uv run pytest tests/test_migrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.migrate'`

- [ ] **Step 3: Implement the migration**

Ordem dos passos, conforme a §11 do spec: notas com renomeação por posição (lendo o trecho do callout, como `tools/check_chapters.py` faz), preenchimento de `kind` a partir da transcrição bruta preservada, quadro com links corrigidos e cartões deduplicados, epub e markdown para `fonte/`, e as bases. Remover `Inbox/`, `Quadros/` e `Books/` só quando ficarem vazios, reportando o que sobrou.

A dedupe compara `(texto do cartão, raia)` e mantém o marcado — um cartão concluído perdido reabre trabalho já feito, o inverso só custa um clique.

O passo de resumos da §11 fica para a Task 13, quando os resumos existirem.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd bridge && uv run pytest tests/test_migrate.py -v`
Expected: PASS, 5 testes.

- [ ] **Step 5: Dry-run against the real vault, then apply**

```bash
cd bridge && uv run python -m handy_bridge.migrate \
  --vault "C:/Users/kakam/OneDrive/Área de Trabalho/Reading"
```
Conferir o plano linha por linha, então repetir com `--apply`.

- [ ] **Step 6: Commit**

```bash
git add bridge/src/handy_bridge/migrate.py bridge/tests/test_migrate.py
git commit -m "feat(bridge): migrar o vault para o layout por livro"
```

---

# FASE 2 — síntese

> **Não começar antes do portão da Task 3 fechar em 5/5.**

## Task 10: Corrigir o recall

**Files:**
- Modify: `bridge/src/handy_bridge/postprocess/__init__.py`
- Modify: `bridge/src/handy_bridge/postprocess/claude_cli.py`
- Modify: `bridge/src/handy_bridge/chapters.py`
- Modify: `bridge/src/handy_bridge/note.py`
- Test: `bridge/tests/test_chapters.py`, `bridge/tests/test_postprocess.py`, `bridge/tests/test_note.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) RecallPoint: said: str, actual: str, correct: bool`
  - `@dataclass(frozen=True) RecallCheck: points: list[RecallPoint], missed: list[str]`
  - `PostProcessor.check_recall(spoken: str, passage: str) -> RecallCheck`
  - `chapters.passage(index, chapter, from_offset, to_offset) -> str`
  - `note.render_recall(check: RecallCheck) -> list[str]`

- [ ] **Step 1: Write the failing test for the passage window**

```python
def test_passage_never_starts_before_the_chapter(IDX=IDX):
    # Muita leitura sem gravar não pode fazer a janela varrer o livro.
    got = chapters_mod.passage(IDX, chapter=3, from_offset=0, to_offset=99999)
    assert got == IDX.get(3).normalized


def test_passage_starts_at_the_last_recall_when_it_is_inside_the_chapter():
    got = chapters_mod.passage(IDX, chapter=2, from_offset=105, to_offset=150)
    assert got and got in IDX.get(2).normalized
    assert not got.startswith(IDX.get(2).normalized[:10])
```

- [ ] **Step 2: Run to verify failure, then implement `passage`**

Run: `cd bridge && uv run pytest tests/test_chapters.py -k passage -v`
Expected: FAIL — `AttributeError: module has no attribute 'passage'`

A janela é `max(from_offset, start_of(chapter))` até `to_offset`, recortada sobre as palavras do capítulo. Devolve o texto do capítulo quando os limites saem dele.

- [ ] **Step 3: Add `check_recall` to the processor and render it**

O `RECALL_PROMPT` recebe o que foi falado e a passagem, e devolve
`{"points": [{"said","actual","correct"}], "missed": [...]}`. Renderização **lado a lado no corpo**, como a §8 do spec decidiu:

```python
def render_recall(check) -> list[str]:
    lines = ["> [!success] Conferência do que você lembrou", ">"]
    for point in check.points:
        mark = "✓" if point.correct else "✗"
        lines.append(f"> {mark} **Você disse:** {point.said}")
        if not point.correct:
            lines.append(f"> **Na verdade:** {point.actual}")
        lines.append(">")
    for item in check.missed:
        lines.append(f"> — **Passou batido:** {item}")
    lines.append("")
    return lines
```

Vale o mesmo aviso de resposta não verificada que as respostas já carregam.

- [ ] **Step 4: Run the suite**

Run: `cd bridge && uv run pytest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/ bridge/tests/
git commit -m "feat(bridge): conferir o recall contra o que foi lido desde a última nota"
```

---

## Task 11: Resumo do capítulo

**Files:**
- Create: `bridge/src/handy_bridge/summaries.py`
- Test: `bridge/tests/test_summaries.py`

**Interfaces:**
- Produces:
  - `OBSERVATIONS_HEADING = "## Minhas observações"`
  - `read_observations(path: Path) -> str`
  - `render_chapter(...) -> str`
  - `write_chapter_summary(path: Path, rendered: str) -> Path`
  - `collect_chapter_notes(notes_dir: Path, chapter: int) -> list[NoteSource]`

O teste que mais importa aqui é a preservação da seção reservada, inclusive quando ela contém cabeçalhos que imitam os gerados — é o caso que um parser ingênuo erra.

- [ ] **Step 1: Write the failing tests**

```python
def test_regeneration_preserves_my_own_observations(tmp_path):
    path = tmp_path / "04 - Cap.md"
    path.write_text(
        "---\ngenerated: true\n---\n\n## Síntese\n\nvelho\n\n"
        "## Minhas observações\n\nisso eu escrevi à mão\n",
        encoding="utf-8",
    )
    kept = summaries.read_observations(path)
    assert kept.strip() == "isso eu escrevi à mão"
    summaries.write_chapter_summary(path, summaries.render_chapter(
        chapter=4, title="Cap", synthesis="novo", notes=[], observations=kept))
    out = path.read_text(encoding="utf-8")
    assert "isso eu escrevi à mão" in out and "novo" in out and "velho" not in out


def test_observations_survive_headings_that_imitate_generated_ones(tmp_path):
    path = tmp_path / "04 - Cap.md"
    path.write_text(
        "## Síntese\n\ns\n\n## Minhas observações\n\n"
        "## Anotações\n\ntexto meu sob um cabeçalho que parece gerado\n",
        encoding="utf-8",
    )
    kept = summaries.read_observations(path)
    assert "cabeçalho que parece gerado" in kept
    assert "## Anotações" in kept


def test_missing_file_yields_empty_observations(tmp_path):
    assert summaries.read_observations(tmp_path / "nao-existe.md") == ""


def test_synthesis_is_capped(tmp_path):
    long = " ".join(["palavra"] * 900)
    out = summaries.render_chapter(chapter=1, title="C", synthesis=long, notes=[], observations="")
    body = out.split("## Anotações")[0]
    assert len(body.split()) <= summaries.SYNTHESIS_WORD_CAP + 40
```

`read_observations` lê **do heading reservado até o fim do arquivo**, não até o próximo `##`. É o que faz o segundo teste passar: a seção é a última por construção, então tudo depois dela é do usuário.

- [ ] **Step 2: Run to verify failure**

Run: `cd bridge && uv run pytest tests/test_summaries.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.summaries'`

- [ ] **Step 3: Implement, with the cap wired to the spec's constraint**

`SYNTHESIS_WORD_CAP = 400`. O teto não é estético: a `## Síntese` é a única seção que alimenta o resumo do livro, e sem teto a entrada do resumo do livro cresce sem controle.

- [ ] **Step 4: Run tests, then commit**

Run: `cd bridge && uv run pytest tests/test_summaries.py -v`
Expected: PASS, 4 testes.

```bash
git add bridge/src/handy_bridge/summaries.py bridge/tests/test_summaries.py
git commit -m "feat(bridge): resumo de capítulo com seção manual preservada"
```

---

## Task 12: Resumo do livro

**Files:**
- Modify: `bridge/src/handy_bridge/summaries.py`
- Test: `bridge/tests/test_summaries.py`

**Interfaces:**
- Produces:
  - `read_synthesis(path: Path) -> str`
  - `collect_syntheses(chapters_dir: Path) -> list[tuple[int, str, str]]`
  - `render_book(title, bullets, syntheses, unrecorded) -> str`
  - `unrecorded_chapters(index, chapters_dir) -> list[tuple[int, str]]`

- [ ] **Step 1: Write the failing tests**

```python
def test_book_summary_is_built_from_syntheses_not_raw_notes(tmp_path):
    ch = tmp_path / "Capítulos"; ch.mkdir()
    (ch / "01 - Um.md").write_text(
        "## Síntese\n\nsintese do um\n\n## Anotações\n\nnota crua que nao deve entrar\n",
        encoding="utf-8")
    got = summaries.collect_syntheses(ch)
    assert got == [(1, "Um", "sintese do um")]
    assert "nota crua" not in got[0][2]


def test_unrecorded_chapters_are_listed(tmp_path):
    ch = tmp_path / "Capítulos"; ch.mkdir()
    (ch / "02 - Dois.md").write_text("## Síntese\n\ns\n", encoding="utf-8")
    missing = summaries.unrecorded_chapters(IDX, ch)
    assert [m[0] for m in missing] == [1, 3]
```

- [ ] **Step 2: Run to verify failure, implement, re-run**

Run: `cd bridge && uv run pytest tests/test_summaries.py -v`
Expected: FAIL, então PASS.

- [ ] **Step 3: Commit**

```bash
git add bridge/src/handy_bridge/summaries.py bridge/tests/test_summaries.py
git commit -m "feat(bridge): resumo do livro reconstruído a partir das sínteses"
```

---

## Task 13: Fiar a Fase 2 e documentar

**Files:**
- Modify: `bridge/src/handy_bridge/pipeline.py`
- Modify: `bridge/src/handy_bridge/config.py`
- Modify: `bridge/config.example.toml`
- Modify: `bridge/src/handy_bridge/migrate.py`
- Modify: `bridge/README.md`
- Test: `bridge/tests/test_pipeline.py`, `bridge/tests/test_config.py`

**Interfaces:**
- Produces: `SummariesConfig(enabled: bool = True)` em `Config.summaries`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_recall_note_gets_its_chapter_summary_regenerated(tmp_path):
    vault = _vault_with_epub(tmp_path, chapters=[("Um", "texto do capitulo um aqui")])
    process_note(_incoming(meta={"clock_synced": True, "book": "livro",
                                 "word_offset": 10, "excerpt": "texto do capitulo um aqui"}),
                 _cfg(vault), transcribe_fn=_fake_transcribe,
                 processor=_fake_processor(kind="recall"))
    summary = vault / "Livros/livro/Capítulos/01 - Um.md"
    assert summary.is_file() and "## Síntese" in summary.read_text(encoding="utf-8")


def test_the_book_summary_rebuilds_only_when_a_chapter_is_left_behind(tmp_path):
    # Duas notas no capítulo 1 não reconstroem; a primeira do 2 reconstrói.
    ...


def test_a_failing_summary_never_costs_the_note(tmp_path):
    path = process_note(..., processor=_processor_that_raises_on_summary())
    assert path.is_file()
```

- [ ] **Step 2: Run to verify failure, then wire the pipeline**

Depois de `write_note`, tudo best-effort e em ordem: regenerar o resumo do capítulo; se o capítulo desta nota for maior que o maior já registrado, reconstruir o resumo do livro. Ambos dentro de `try/except (PostProcessError, OSError)` com `log.warning`.

- [ ] **Step 3: Add the config section and the migration step**

`[summaries] enabled = true` em `config.py` e `config.example.toml`. O passo 6 da §11 do spec — gerar os resumos a partir das notas migradas — entra em `migrate.py` agora que os resumos existem.

- [ ] **Step 4: Update the README**

Layout novo, `kind` e as variantes faladas, as três views, os dois resumos, o atraso conhecido do resumo do livro, e o custo real medido por nota. Corrigir a seção "Formato da nota", que hoje mostra frontmatter sem `kind` nem capítulo.

- [ ] **Step 5: Run everything**

Run: `cd bridge && uv run pytest -v`
Expected: PASS, suíte inteira.

- [ ] **Step 6: Commit**

```bash
git add bridge/ 
git commit -m "feat(bridge): resumos de capítulo e de livro no pipeline"
```

---

## Self-Review

**Cobertura do spec:**

| Seção do spec | Task |
|---|---|
| §4 layout | 5, 7, 9 |
| §4.1 fonte vs derivado | 5, 11 |
| §4.2 nome por posição | 5, 6 |
| §4.3 frontmatter | 6 |
| §5 kind em duas camadas | 4 |
| §6 resolução de capítulo | 1, 2, **3 (portão)** |
| §7 correção de recall | 10 |
| §8 resumo do capítulo | 11 |
| §9 resumo do livro | 12 |
| §10 views de Bases | 8 |
| §11 migração | 9, 13 (passo 6) |
| §12 testes | em toda task |
| §14 fases | estrutura do plano |

**Consistência de tipos:** `Resolution.chapter` é `int` e alimenta `NoteData.chapter: int | None`; `BookIndex.width` alimenta o `width` de `write_note` e de `chapter_summary_path`; `NoteKind` é definido em `kind.py` e importado por `postprocess` e `note`, nunca redefinido.

**Ciclo de import conhecido e resolvido:** `layout` importa `slugify` de `note`, e `note.write_note` precisa de `layout.note_stem`. Resolvido com import local dentro da função, documentado na Task 6 Step 4.

**Ponto único não verificável por teste:** a sintaxe de filtro do `.base` (Task 8), com mitigação estrutural descrita na própria task.
