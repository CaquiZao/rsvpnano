# handy-bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Um serviço local que recebe um WAV do RSVP Nano, transcreve com o Handy/Nemotron e escreve uma nota Markdown com título e tags no vault Obsidian `Reading`.

**Architecture:** Serviço Python único. FastAPI recebe `POST /v1/notes` (multipart), persiste o WAV e responde `200` imediatamente; a transcrição roda em background. O pipeline é uma cadeia de módulos com responsabilidade única — inspeção de WAV, transcrição, pós-processamento plugável, renderização de nota — de modo que cada peça é testável sem hardware e sem rede.

**Tech Stack:** Python 3.14, `uv`, FastAPI, uvicorn, python-multipart, zeroconf, pytest. Handy CLI e `claude` CLI como subprocessos.

**Spec:** [`docs/superpowers/specs/2026-09-07-notas-de-voz-obsidian-design.md`](../specs/2026-09-07-notas-de-voz-obsidian-design.md)

## Global Constraints

Valores verificados por execução nesta máquina. Copiar exatamente.

- Handy executável: `C:/Users/kakam/AppData/Local/Handy/handy.exe`
- Modelo ASR: `handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf/nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf` (o ID inclui o nome do `.gguf`, não só o repositório)
- Handy escreve **logs em stderr e JSON em stdout**; sai com código 0. Parsear apenas stdout.
- Saída do Handy: `{"audio_secs":float,"best_ms":int,"load_ms":int,"model":str,"rtf":float,"text":str,"transcribe_ms":[int]}` — o campo usado é `text`
- Saída do `claude -p --output-format json`: wrapper com `result` (string), `is_error` (bool), `subtype` (str). O JSON do modelo está **dentro** da string `result`.
- Modelo padrão de pós-processamento: `claude-haiku-4-5-20251001`
- Vault: `C:/Users/kakam/OneDrive/Área de Trabalho/Reading`, pasta de destino `Inbox`
- Áudio é sempre WAV PCM **16 kHz, mono, 16-bit**
- Toda escrita no vault é **atômica** (arquivo temporário + `os.replace`), porque o vault fica dentro do OneDrive
- **Uma falha no pós-processamento nunca pode custar uma nota** — degrada para transcrição crua com título por timestamp
- Todo o código, nomes de símbolos e mensagens de commit em inglês; comentários e docstrings também. (O plano e a spec são em português; o código segue o upstream.)

---

### Task 1: Scaffold do projeto e carregamento de configuração

**Files:**
- Create: `bridge/pyproject.toml`
- Create: `bridge/config.example.toml`
- Create: `bridge/src/handy_bridge/__init__.py`
- Create: `bridge/src/handy_bridge/config.py`
- Test: `bridge/tests/test_config.py`

**Interfaces:**
- Consumes: nada (primeira task)
- Produces: `Config`, `AsrConfig`, `PostProcessConfig`, `load_config(path: Path) -> Config`, `ConfigError`

- [ ] **Step 1: Criar o projeto com uv**

```bash
cd bridge
uv init --name handy-bridge --lib
uv add fastapi uvicorn python-multipart zeroconf
uv add --dev pytest httpx
```

`httpx` é obrigatório: o `TestClient` do FastAPI (usado na Task 7) depende dele e não vem junto com o `fastapi`.

- [ ] **Step 2: Escrever o teste que falha**

```python
# bridge/tests/test_config.py
from pathlib import Path
import pytest
from handy_bridge.config import load_config, ConfigError


def write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_loads_all_fields(tmp_path):
    vault = tmp_path / "Reading"
    vault.mkdir()
    cfg = load_config(write(tmp_path, f"""
vault_path   = "{vault.as_posix()}"
inbox_folder = "Inbox"
audio_store  = "{(tmp_path / 'audio').as_posix()}"
port         = 8787

[asr]
handy_exe = "{(tmp_path / 'handy.exe').as_posix()}"
model     = "handy-computer/x/y.gguf"
timeout_s = 900

[post_process]
enabled = true
backend = "claude_cli"
model   = "claude-haiku-4-5-20251001"
"""))
    assert cfg.vault_path == vault
    assert cfg.inbox_folder == "Inbox"
    assert cfg.port == 8787
    assert cfg.asr.model == "handy-computer/x/y.gguf"
    assert cfg.asr.timeout_s == 900
    assert cfg.post_process.backend == "claude_cli"
    assert cfg.post_process.model == "claude-haiku-4-5-20251001"


def test_creates_audio_store_if_missing(tmp_path):
    vault = tmp_path / "Reading"
    vault.mkdir()
    store = tmp_path / "nested" / "audio"
    cfg = load_config(write(tmp_path, f"""
vault_path   = "{vault.as_posix()}"
inbox_folder = "Inbox"
audio_store  = "{store.as_posix()}"
port         = 8787

[asr]
handy_exe = "{(tmp_path / 'handy.exe').as_posix()}"
model     = "m"
timeout_s = 900

[post_process]
enabled = false
backend = "none"
model   = ""
"""))
    assert cfg.audio_store.is_dir()


def test_rejects_missing_vault(tmp_path):
    with pytest.raises(ConfigError, match="vault_path"):
        load_config(write(tmp_path, f"""
vault_path   = "{(tmp_path / 'nope').as_posix()}"
inbox_folder = "Inbox"
audio_store  = "{(tmp_path / 'audio').as_posix()}"
port         = 8787

[asr]
handy_exe = "h"
model     = "m"
timeout_s = 900

[post_process]
enabled = false
backend = "none"
model   = ""
"""))


def test_rejects_unknown_backend(tmp_path):
    vault = tmp_path / "Reading"
    vault.mkdir()
    with pytest.raises(ConfigError, match="backend"):
        load_config(write(tmp_path, f"""
vault_path   = "{vault.as_posix()}"
inbox_folder = "Inbox"
audio_store  = "{(tmp_path / 'audio').as_posix()}"
port         = 8787

[asr]
handy_exe = "h"
model     = "m"
timeout_s = 900

[post_process]
enabled = true
backend = "gpt5"
model   = ""
"""))
```

- [ ] **Step 3: Rodar o teste e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.config'`

- [ ] **Step 4: Implementar `config.py`**

```python
# bridge/src/handy_bridge/config.py
"""Load and validate the bridge's config.toml."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

VALID_BACKENDS = {"claude_cli", "anthropic_api", "ollama", "none"}


class ConfigError(Exception):
    """Raised when config.toml is missing a field or holds an invalid value."""


@dataclass(frozen=True)
class AsrConfig:
    handy_exe: Path
    model: str
    timeout_s: int


@dataclass(frozen=True)
class PostProcessConfig:
    enabled: bool
    backend: str
    model: str


@dataclass(frozen=True)
class Config:
    vault_path: Path
    inbox_folder: str
    audio_store: Path
    port: int
    asr: AsrConfig
    post_process: PostProcessConfig

    @property
    def inbox_path(self) -> Path:
        return self.vault_path / self.inbox_folder


def _require(table: dict, key: str, where: str):
    if key not in table:
        raise ConfigError(f"missing '{key}' in {where}")
    return table[key]


def load_config(path: Path) -> Config:
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    vault_path = Path(_require(raw, "vault_path", "config")).expanduser()
    if not vault_path.is_dir():
        raise ConfigError(f"vault_path does not exist: {vault_path}")

    audio_store = Path(_require(raw, "audio_store", "config")).expanduser()
    audio_store.mkdir(parents=True, exist_ok=True)

    asr_raw = _require(raw, "asr", "config")
    pp_raw = _require(raw, "post_process", "config")

    backend = _require(pp_raw, "backend", "[post_process]")
    if backend not in VALID_BACKENDS:
        raise ConfigError(
            f"unknown backend '{backend}'; expected one of {sorted(VALID_BACKENDS)}"
        )

    return Config(
        vault_path=vault_path,
        inbox_folder=_require(raw, "inbox_folder", "config"),
        audio_store=audio_store,
        port=int(_require(raw, "port", "config")),
        asr=AsrConfig(
            handy_exe=Path(_require(asr_raw, "handy_exe", "[asr]")).expanduser(),
            model=_require(asr_raw, "model", "[asr]"),
            timeout_s=int(_require(asr_raw, "timeout_s", "[asr]")),
        ),
        post_process=PostProcessConfig(
            enabled=bool(_require(pp_raw, "enabled", "[post_process]")),
            backend=backend,
            model=pp_raw.get("model", ""),
        ),
    )
```

- [ ] **Step 5: Criar `config.example.toml`**

```toml
# bridge/config.example.toml
vault_path   = "C:/Users/kakam/OneDrive/Área de Trabalho/Reading"
inbox_folder = "Inbox"
audio_store  = "C:/Users/kakam/.handy-bridge/audio"
port         = 8787

[asr]
handy_exe = "C:/Users/kakam/AppData/Local/Handy/handy.exe"
model     = "handy-computer/nemotron-3.5-asr-streaming-0.6b-gguf/nemotron-3.5-asr-streaming-0.6b-Q8_0.gguf"
timeout_s = 900

[post_process]
enabled = true
backend = "claude_cli"          # claude_cli | anthropic_api | ollama | none
model   = "claude-haiku-4-5-20251001"
```

- [ ] **Step 6: Rodar os testes e confirmar que passam**

Run: `cd bridge && uv run pytest tests/test_config.py -v`
Expected: PASS (4 testes)

- [ ] **Step 7: Commit**

```bash
git add bridge/
git commit -m "feat(bridge): scaffold project and config loading"
```

---

### Task 2: Inspeção e reparo de WAV

O firmware pode morrer sem fechar o arquivo, deixando os tamanhos do cabeçalho zerados. Perder a nota por isso é inaceitável — o bridge conserta usando o tamanho real do arquivo.

**Files:**
- Create: `bridge/src/handy_bridge/wav.py`
- Test: `bridge/tests/test_wav.py`

**Interfaces:**
- Consumes: nada
- Produces: `WavInfo(sample_rate, channels, bits_per_sample, data_bytes)` com `duration_s` como propriedade calculada, `inspect(path) -> WavInfo`, `repair_header(path) -> bool`, `InvalidWav`, `HEADER_SIZE = 44`

- [ ] **Step 1: Escrever o teste que falha**

```python
# bridge/tests/test_wav.py
import struct
from pathlib import Path
import pytest
from handy_bridge.wav import inspect, repair_header, InvalidWav

SR, CH, BITS = 16000, 1, 16


def make_wav(path: Path, frames: int, *, zero_sizes: bool = False) -> Path:
    data = b"\x00\x01" * frames
    data_len = 0 if zero_sizes else len(data)
    riff_len = 0 if zero_sizes else 36 + len(data)
    header = (
        b"RIFF" + struct.pack("<I", riff_len) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, CH, SR, SR * CH * BITS // 8, CH * BITS // 8, BITS)
        + b"data" + struct.pack("<I", data_len)
    )
    path.write_bytes(header + data)
    return path


def test_inspect_reads_format(tmp_path):
    info = inspect(make_wav(tmp_path / "a.wav", SR))  # 1 second
    assert info.sample_rate == SR
    assert info.channels == CH
    assert info.bits_per_sample == BITS
    assert info.data_bytes == SR * 2
    assert info.duration_s == pytest.approx(1.0)


def test_repair_fixes_zeroed_sizes(tmp_path):
    p = make_wav(tmp_path / "b.wav", SR, zero_sizes=True)
    assert repair_header(p) is True
    info = inspect(p)
    assert info.data_bytes == SR * 2
    assert info.duration_s == pytest.approx(1.0)


def test_repair_is_noop_on_healthy_file(tmp_path):
    p = make_wav(tmp_path / "c.wav", SR)
    before = p.read_bytes()
    assert repair_header(p) is False
    assert p.read_bytes() == before


def test_rejects_non_riff(tmp_path):
    p = tmp_path / "d.wav"
    p.write_bytes(b"NOPE" + b"\x00" * 100)
    with pytest.raises(InvalidWav, match="RIFF"):
        inspect(p)


def test_rejects_file_shorter_than_header(tmp_path):
    p = tmp_path / "e.wav"
    p.write_bytes(b"RIFF")
    with pytest.raises(InvalidWav):
        inspect(p)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_wav.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.wav'`

- [ ] **Step 3: Implementar `wav.py`**

```python
# bridge/src/handy_bridge/wav.py
"""Inspect and repair canonical PCM WAV files written by the device."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

HEADER_SIZE = 44
_RIFF_SIZE_OFFSET = 4
_DATA_SIZE_OFFSET = 40


class InvalidWav(Exception):
    """Raised when a file is not a usable 44-byte-header PCM WAV."""


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    bits_per_sample: int
    data_bytes: int

    @property
    def duration_s(self) -> float:
        frame_bytes = self.channels * self.bits_per_sample // 8
        if frame_bytes == 0 or self.sample_rate == 0:
            return 0.0
        return self.data_bytes / frame_bytes / self.sample_rate


def _read_header(path: Path) -> bytes:
    raw = Path(path).read_bytes()[:HEADER_SIZE]
    if len(raw) < HEADER_SIZE:
        raise InvalidWav(f"file shorter than a WAV header: {path}")
    if raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise InvalidWav(f"missing RIFF/WAVE magic: {path}")
    return raw


def inspect(path: Path) -> WavInfo:
    raw = _read_header(path)
    channels, sample_rate = struct.unpack_from("<HI", raw, 22)
    (bits_per_sample,) = struct.unpack_from("<H", raw, 34)
    (data_bytes,) = struct.unpack_from("<I", raw, _DATA_SIZE_OFFSET)
    actual = Path(path).stat().st_size - HEADER_SIZE
    # Trust the file over the header: a truncated recording reports more than it has.
    if data_bytes == 0 or data_bytes > actual:
        data_bytes = max(actual, 0)
    return WavInfo(sample_rate, channels, bits_per_sample, data_bytes)


def repair_header(path: Path) -> bool:
    """Patch zeroed RIFF/data sizes from the real file size. Returns True if patched."""
    path = Path(path)
    raw = _read_header(path)
    (declared,) = struct.unpack_from("<I", raw, _DATA_SIZE_OFFSET)
    actual = path.stat().st_size - HEADER_SIZE
    if actual <= 0:
        raise InvalidWav(f"no audio payload: {path}")
    if declared == actual:
        return False
    with path.open("r+b") as fh:
        fh.seek(_RIFF_SIZE_OFFSET)
        fh.write(struct.pack("<I", 36 + actual))
        fh.seek(_DATA_SIZE_OFFSET)
        fh.write(struct.pack("<I", actual))
    return True
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd bridge && uv run pytest tests/test_wav.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/wav.py bridge/tests/test_wav.py
git commit -m "feat(bridge): inspect and repair truncated WAV headers"
```

---

### Task 3: Renderização e escrita atômica da nota

**Files:**
- Create: `bridge/src/handy_bridge/note.py`
- Test: `bridge/tests/test_note.py`

**Interfaces:**
- Consumes: nada
- Produces: `NoteData` (com `book`, `word_offset`, `excerpt` opcionais, `None` quando a nota não nasceu dentro do leitor), `slugify(text) -> str`, `render(note: NoteData) -> str`, `write_note(inbox: Path, note: NoteData) -> Path`

- [ ] **Step 1: Escrever o teste que falha**

```python
# bridge/tests/test_note.py
from datetime import datetime
from pathlib import Path
from handy_bridge.note import NoteData, render, write_note, slugify


def sample(**over) -> NoteData:
    base = dict(
        title="Ideia de captura por voz",
        tags=["ideia", "projeto/rsvpnano"],
        body="Texto limpo.",
        raw_transcript="entao eh tipo assim texto cru",
        recorded_at=datetime(2026, 9, 7, 14, 32, 11),
        date_estimated=False,
        duration_s=47.0,
        asr_model="nemotron-3.5-asr-streaming-0.6b",
        book=None,
        word_offset=None,
        excerpt=None,
    )
    base.update(over)
    return NoteData(**base)


def test_render_has_frontmatter_and_raw_callout():
    out = render(sample())
    assert out.startswith("---\n")
    assert 'title: "Ideia de captura por voz"' in out
    assert "date: 2026-09-07T14:32:11" in out
    assert "duration: 47s" in out
    assert "source: rsvp-nano" in out
    assert "tags: [ideia, projeto/rsvpnano]" in out
    assert "Texto limpo." in out
    assert "> [!note]- Transcrição original" in out
    assert "> entao eh tipo assim texto cru" in out
    assert "date_estimated" not in out


def test_render_flags_estimated_date():
    assert "date_estimated: true" in render(sample(date_estimated=True))


def test_render_escapes_quotes_in_title():
    assert 'title: "Ele disse \\"oi\\""' in render(sample(title='Ele disse "oi"'))


def test_multiline_raw_transcript_is_fully_quoted():
    out = render(sample(raw_transcript="linha um\nlinha dois"))
    assert "> linha um\n> linha dois" in out


def test_render_includes_book_anchor_when_present():
    out = render(sample(book="epdf.pub_sapiens", word_offset=12438,
                        excerpt="a Revolução Agrícola foi a maior fraude da história"))
    assert 'book: "[[epdf.pub_sapiens]]"' in out
    assert "word_offset: 12438" in out
    assert "> [!quote] Trecho que eu estava lendo" in out
    assert "> a Revolução Agrícola foi a maior fraude da história" in out


def test_render_omits_book_anchor_for_standalone_note():
    out = render(sample())
    assert "book:" not in out
    assert "word_offset:" not in out
    assert "[!quote]" not in out


def test_render_allows_word_offset_zero():
    # offset 0 is the first word of the book, not "absent"
    assert "word_offset: 0" in render(sample(book="b", word_offset=0))


def test_render_quotes_excerpt_even_without_book():
    out = render(sample(excerpt="linha um\nlinha dois"))
    assert "> linha um\n> linha dois" in out
    assert "book:" not in out


def test_slugify_replaces_reserved_path_chars_and_keeps_accents():
    assert slugify("Ideia: captura/voz?") == "Ideia captura-voz"
    assert slugify("Gravação de áudio") == "Gravação de áudio"
    assert slugify("   ") == "Sem titulo"


def test_write_note_creates_inbox_and_names_file(tmp_path):
    path = write_note(tmp_path / "Inbox", sample())
    assert path.parent.is_dir()
    assert path.name == "2026-09-07 1432 - Ideia de captura por voz.md"
    assert "Texto limpo." in path.read_text(encoding="utf-8")


def test_write_note_never_overwrites(tmp_path):
    inbox = tmp_path / "Inbox"
    first = write_note(inbox, sample())
    second = write_note(inbox, sample())
    assert first != second
    assert second.name.endswith("-2.md")


def test_write_note_leaves_no_temp_files(tmp_path):
    inbox = tmp_path / "Inbox"
    write_note(inbox, sample())
    assert [p.name for p in inbox.iterdir() if p.suffix != ".md"] == []
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_note.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.note'`

- [ ] **Step 3: Implementar `note.py`**

```python
# bridge/src/handy_bridge/note.py
"""Render voice notes as Obsidian Markdown and write them atomically."""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_FORBIDDEN = '<>:"\\|?*'


@dataclass(frozen=True)
class NoteData:
    title: str
    tags: list[str]
    body: str
    raw_transcript: str
    recorded_at: datetime
    date_estimated: bool
    duration_s: float
    asr_model: str
    # Present only when the recording was triggered from inside the reader.
    book: str | None = None
    word_offset: int | None = None
    excerpt: str | None = None


def slugify(text: str) -> str:
    """Make a title safe for a Windows filename without mangling accents."""
    # '/' becomes a hyphen so a path-like title stays readable; the rest of the
    # reserved characters collapse to spaces. Order matters: '/' must be handled
    # before the blanket replacement, or it would turn into a space too.
    cleaned = text.replace("/", "-")
    cleaned = "".join(" " if ch in _FORBIDDEN else ch for ch in cleaned)
    cleaned = " ".join(cleaned.split())
    cleaned = "".join(
        ch for ch in cleaned if unicodedata.category(ch)[0] != "C"
    )
    return cleaned[:80].strip() or "Sem titulo"


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(note: NoteData) -> str:
    lines = [
        "---",
        f"title: {_quote(note.title)}",
        f"date: {note.recorded_at.isoformat(timespec='seconds')}",
        f"duration: {round(note.duration_s)}s",
        "source: rsvp-nano",
        f"asr_model: {note.asr_model}",
        f"tags: [{', '.join(note.tags)}]",
    ]
    if note.book:
        lines.append(f'book: "[[{note.book}]]"')
    # Offset 0 is the first word of the book, so compare against None explicitly.
    if note.word_offset is not None:
        lines.append(f"word_offset: {note.word_offset}")
    if note.date_estimated:
        lines.append("date_estimated: true")
    lines += ["---", "", note.body.strip(), ""]

    excerpt = (note.excerpt or "").strip()
    if excerpt:
        lines.append("> [!quote] Trecho que eu estava lendo")
        lines += [f"> {line}" for line in excerpt.splitlines()]
        lines.append("")

    raw = note.raw_transcript.strip()
    if raw:
        lines.append("> [!note]- Transcrição original")
        lines += [f"> {line}" for line in raw.splitlines()]
        lines.append("")
    return "\n".join(lines)


def _unique_path(inbox: Path, stem: str) -> Path:
    candidate = inbox / f"{stem}.md"
    counter = 2
    while candidate.exists():
        candidate = inbox / f"{stem}-{counter}.md"
        counter += 1
    return candidate


def write_note(inbox: Path, note: NoteData) -> Path:
    """Write the note atomically so OneDrive never syncs a partial file."""
    inbox = Path(inbox)
    inbox.mkdir(parents=True, exist_ok=True)
    stem = f"{note.recorded_at:%Y-%m-%d %H%M} - {slugify(note.title)}"
    target = _unique_path(inbox, stem)

    tmp = target.with_name(target.name + ".partial")
    tmp.write_text(render(note), encoding="utf-8")
    os.replace(tmp, target)
    return target
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd bridge && uv run pytest tests/test_note.py -v`
Expected: PASS (12 testes)

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/note.py bridge/tests/test_note.py
git commit -m "feat(bridge): render and atomically write Obsidian notes"
```

---

### Task 4: Pós-processamento plugável (interface + backend `claude_cli`)

**Files:**
- Create: `bridge/src/handy_bridge/postprocess/__init__.py`
- Create: `bridge/src/handy_bridge/postprocess/claude_cli.py`
- Test: `bridge/tests/test_postprocess.py`

**Interfaces:**
- Consumes: `PostProcessConfig` (Task 1)
- Produces: `PostProcessResult(title, tags, cleaned)`, `PostProcessor` (Protocol com `process(transcript: str) -> PostProcessResult`), `PostProcessError`, `build(cfg: PostProcessConfig) -> PostProcessor | None`, `ClaudeCliProcessor(model: str, runner=...)`, `extract_json_object(text: str) -> dict`

- [ ] **Step 1: Escrever o teste que falha**

```python
# bridge/tests/test_postprocess.py
import json
import pytest
from handy_bridge.config import PostProcessConfig
from handy_bridge.postprocess import build, PostProcessError, PostProcessResult
from handy_bridge.postprocess.claude_cli import ClaudeCliProcessor, extract_json_object


class FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout, self.returncode, self.stderr = stdout, returncode, ""


def wrapper(result_text: str, is_error: bool = False) -> str:
    return json.dumps({"result": result_text, "is_error": is_error, "subtype": "success"})


def test_parses_wrapper_then_inner_json():
    inner = '{"title": "Captura por voz", "tags": ["ideia"], "cleaned": "Texto limpo."}'
    proc = ClaudeCliProcessor("claude-haiku-4-5-20251001",
                              runner=lambda cmd, timeout: FakeCompleted(wrapper(inner)))
    out = proc.process("texto cru")
    assert out == PostProcessResult(title="Captura por voz", tags=["ideia"], cleaned="Texto limpo.")


def test_tolerates_code_fences_around_inner_json():
    inner = '```json\n{"title": "T", "tags": [], "cleaned": "C"}\n```'
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner)))
    assert proc.process("x").title == "T"


def test_passes_model_flag_and_transcript():
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return FakeCompleted(wrapper('{"title":"T","tags":[],"cleaned":"C"}'))

    ClaudeCliProcessor("claude-haiku-4-5-20251001", runner=runner).process("minha fala")
    assert "--model" in seen["cmd"]
    assert "claude-haiku-4-5-20251001" in seen["cmd"]
    assert "--output-format" in seen["cmd"] and "json" in seen["cmd"]
    assert any("minha fala" in part for part in seen["cmd"])


def test_raises_when_cli_reports_error():
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted(wrapper("boom", True)))
    with pytest.raises(PostProcessError, match="reported an error"):
        proc.process("x")


def test_raises_on_nonzero_exit():
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted("", 1))
    with pytest.raises(PostProcessError, match="exit code 1"):
        proc.process("x")


def test_raises_when_inner_json_is_not_an_object():
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted(wrapper("desculpe, nao consigo")))
    with pytest.raises(PostProcessError, match="no JSON object"):
        proc.process("x")


def test_raises_when_required_key_missing():
    inner = '{"title": "T", "cleaned": "C"}'
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner)))
    with pytest.raises(PostProcessError, match="tags"):
        proc.process("x")


def test_coerces_tags_to_list_of_strings():
    inner = '{"title": "T", "tags": ["a", 2, null], "cleaned": "C"}'
    proc = ClaudeCliProcessor("m", runner=lambda cmd, timeout: FakeCompleted(wrapper(inner)))
    assert proc.process("x").tags == ["a", "2"]


def test_extract_json_object_finds_embedded_object():
    assert extract_json_object('bla {"a": 1} bla') == {"a": 1}


def test_build_returns_none_when_disabled():
    assert build(PostProcessConfig(enabled=False, backend="claude_cli", model="m")) is None


def test_build_returns_none_for_none_backend():
    assert build(PostProcessConfig(enabled=True, backend="none", model="")) is None


def test_build_returns_claude_processor():
    got = build(PostProcessConfig(enabled=True, backend="claude_cli", model="m"))
    assert isinstance(got, ClaudeCliProcessor)


def test_build_rejects_unimplemented_backend():
    with pytest.raises(PostProcessError, match="not implemented"):
        build(PostProcessConfig(enabled=True, backend="ollama", model="m"))
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_postprocess.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.postprocess'`

- [ ] **Step 3: Implementar a interface**

```python
# bridge/src/handy_bridge/postprocess/__init__.py
"""Pluggable post-processing: turn a raw transcript into title, tags and clean text."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from handy_bridge.config import PostProcessConfig


class PostProcessError(Exception):
    """Raised when a post-processing backend fails or returns unusable output."""


@dataclass(frozen=True)
class PostProcessResult:
    title: str
    tags: list[str]
    cleaned: str


class PostProcessor(Protocol):
    def process(self, transcript: str) -> PostProcessResult: ...


def build(cfg: PostProcessConfig) -> PostProcessor | None:
    """Return a processor, or None when post-processing is switched off."""
    if not cfg.enabled or cfg.backend == "none":
        return None
    if cfg.backend == "claude_cli":
        from handy_bridge.postprocess.claude_cli import ClaudeCliProcessor

        return ClaudeCliProcessor(cfg.model)
    raise PostProcessError(f"backend '{cfg.backend}' is not implemented yet")
```

- [ ] **Step 4: Implementar o backend `claude_cli`**

```python
# bridge/src/handy_bridge/postprocess/claude_cli.py
"""Post-process a transcript by shelling out to the `claude` CLI."""
from __future__ import annotations

import json
import subprocess
from typing import Callable

from handy_bridge.postprocess import PostProcessError, PostProcessResult

PROMPT = (
    "Você recebe a transcrição bruta de uma nota de voz em português. "
    "Responda APENAS com um objeto JSON válido, sem cercas de código, no formato "
    '{"title": string, "tags": array de strings, "cleaned": string}. '
    '"title" é um título curto e descritivo. "tags" são de 2 a 5 tags em minúsculas. '
    '"cleaned" é a transcrição com pontuação corrigida e hesitações removidas, '
    "preservando o sentido e sem inventar informação.\n\nTranscrição:\n"
)

_REQUIRED = ("title", "tags", "cleaned")


def extract_json_object(text: str) -> dict:
    """Pull the first balanced JSON object out of a string, ignoring code fences."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(text[start : idx + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
        start = text.find("{", start + 1)
    raise PostProcessError("no JSON object found in model output")


def _default_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )


class ClaudeCliProcessor:
    def __init__(
        self,
        model: str,
        runner: Callable[[list[str], int], subprocess.CompletedProcess] = _default_runner,
        timeout_s: int = 120,
    ):
        self._model = model
        self._runner = runner
        self._timeout_s = timeout_s

    def process(self, transcript: str) -> PostProcessResult:
        cmd = [
            "claude", "-p", PROMPT + transcript,
            "--output-format", "json",
            "--model", self._model,
        ]
        try:
            completed = self._runner(cmd, self._timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise PostProcessError(f"claude CLI timed out after {self._timeout_s}s") from exc
        except FileNotFoundError as exc:
            raise PostProcessError("claude CLI not found on PATH") from exc

        if completed.returncode != 0:
            raise PostProcessError(f"claude CLI failed with exit code {completed.returncode}")

        try:
            wrapper = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise PostProcessError("claude CLI did not return JSON") from exc

        if wrapper.get("is_error"):
            raise PostProcessError(f"claude CLI reported an error: {wrapper.get('result')!r}")

        payload = extract_json_object(str(wrapper.get("result", "")))
        missing = [key for key in _REQUIRED if key not in payload]
        if missing:
            raise PostProcessError(f"model output missing keys: {', '.join(missing)}")

        tags = [str(tag) for tag in payload["tags"] if tag is not None]
        return PostProcessResult(
            title=str(payload["title"]).strip(),
            tags=tags,
            cleaned=str(payload["cleaned"]).strip(),
        )
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `cd bridge && uv run pytest tests/test_postprocess.py -v`
Expected: PASS (13 testes)

- [ ] **Step 6: Commit**

```bash
git add bridge/src/handy_bridge/postprocess bridge/tests/test_postprocess.py
git commit -m "feat(bridge): pluggable post-processing with claude CLI backend"
```

---

### Task 5: Transcrição via Handy CLI

**Files:**
- Create: `bridge/src/handy_bridge/transcriber.py`
- Test: `bridge/tests/test_transcriber.py`

**Interfaces:**
- Consumes: `AsrConfig` (Task 1)
- Produces: `Transcription(text, audio_secs, rtf, model)`, `TranscriptionError`, `transcribe(wav: Path, cfg: AsrConfig, runner=...) -> Transcription`

- [ ] **Step 1: Escrever o teste que falha**

```python
# bridge/tests/test_transcriber.py
import json
from pathlib import Path
import pytest
from handy_bridge.config import AsrConfig
from handy_bridge.transcriber import transcribe, Transcription, TranscriptionError


def cfg(tmp_path) -> AsrConfig:
    return AsrConfig(handy_exe=tmp_path / "handy.exe", model="m/model.gguf", timeout_s=900)


class FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


HANDY_OUT = json.dumps({
    "audio_secs": 2.0, "best_ms": 1508, "load_ms": 1366,
    "model": "m/model.gguf", "rtf": 1.326, "text": "ola mundo", "transcribe_ms": [1508],
})


def test_parses_stdout(tmp_path):
    got = transcribe(tmp_path / "a.wav", cfg(tmp_path),
                     runner=lambda cmd, timeout: FakeCompleted(HANDY_OUT))
    assert got == Transcription(text="ola mundo", audio_secs=2.0, rtf=1.326, model="m/model.gguf")


def test_ignores_stderr_logs(tmp_path):
    noisy = FakeCompleted(HANDY_OUT, stderr="[INFO] loaded Vulkan backend\n[INFO] done\n")
    assert transcribe(tmp_path / "a.wav", cfg(tmp_path),
                      runner=lambda cmd, timeout: noisy).text == "ola mundo"


def test_builds_expected_command(tmp_path):
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"], seen["timeout"] = cmd, timeout
        return FakeCompleted(HANDY_OUT)

    wav = tmp_path / "note.wav"
    transcribe(wav, cfg(tmp_path), runner=runner)
    assert seen["cmd"][0] == str(tmp_path / "handy.exe")
    assert "--transcribe-file" in seen["cmd"] and str(wav) in seen["cmd"]
    assert "--json" in seen["cmd"]
    assert "--model" in seen["cmd"] and "m/model.gguf" in seen["cmd"]
    assert seen["timeout"] == 900


def test_empty_text_is_allowed(tmp_path):
    out = json.dumps({"audio_secs": 1.0, "rtf": 1.0, "model": "m", "text": ""})
    assert transcribe(tmp_path / "a.wav", cfg(tmp_path),
                      runner=lambda cmd, timeout: FakeCompleted(out)).text == ""


def test_raises_on_nonzero_exit(tmp_path):
    with pytest.raises(TranscriptionError, match="exit code 2"):
        transcribe(tmp_path / "a.wav", cfg(tmp_path),
                   runner=lambda cmd, timeout: FakeCompleted("", 2, "model not installed"))


def test_raises_on_unparseable_stdout(tmp_path):
    with pytest.raises(TranscriptionError, match="JSON"):
        transcribe(tmp_path / "a.wav", cfg(tmp_path),
                   runner=lambda cmd, timeout: FakeCompleted("not json"))


def test_raises_when_text_key_absent(tmp_path):
    with pytest.raises(TranscriptionError, match="text"):
        transcribe(tmp_path / "a.wav", cfg(tmp_path),
                   runner=lambda cmd, timeout: FakeCompleted('{"audio_secs": 1.0}'))
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_transcriber.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.transcriber'`

- [ ] **Step 3: Implementar `transcriber.py`**

```python
# bridge/src/handy_bridge/transcriber.py
"""Transcribe a WAV by shelling out to the Handy CLI.

Handy writes its logs to stderr and a single JSON object to stdout, and exits 0.
Only stdout is parsed.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from handy_bridge.config import AsrConfig


class TranscriptionError(Exception):
    """Raised when Handy fails or returns output the bridge cannot use."""


@dataclass(frozen=True)
class Transcription:
    text: str
    audio_secs: float
    rtf: float
    model: str


def _default_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )


def transcribe(
    wav: Path,
    cfg: AsrConfig,
    runner: Callable[[list[str], int], subprocess.CompletedProcess] = _default_runner,
) -> Transcription:
    cmd = [
        str(cfg.handy_exe),
        "--transcribe-file", str(wav),
        "--json",
        "--model", cfg.model,
    ]
    try:
        completed = runner(cmd, cfg.timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError(f"Handy timed out after {cfg.timeout_s}s") from exc
    except FileNotFoundError as exc:
        raise TranscriptionError(f"Handy executable not found: {cfg.handy_exe}") from exc

    if completed.returncode != 0:
        raise TranscriptionError(
            f"Handy failed with exit code {completed.returncode}: "
            f"{(completed.stderr or '').strip()[-300:]}"
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TranscriptionError("Handy stdout was not valid JSON") from exc

    if "text" not in payload:
        raise TranscriptionError("Handy output has no 'text' field")

    return Transcription(
        text=str(payload["text"]).strip(),
        audio_secs=float(payload.get("audio_secs", 0.0)),
        rtf=float(payload.get("rtf", 0.0)),
        model=str(payload.get("model", cfg.model)),
    )
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd bridge && uv run pytest tests/test_transcriber.py -v`
Expected: PASS (7 testes)

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/transcriber.py bridge/tests/test_transcriber.py
git commit -m "feat(bridge): transcribe WAVs through the Handy CLI"
```

---

### Task 6: Pipeline — reconstrução de relógio, orquestração e degradação

Esta é a task onde as regras da spec viram comportamento: reconstruir a hora quando o
device não tinha relógio, e nunca perder uma nota porque o LLM falhou.

**Files:**
- Create: `bridge/src/handy_bridge/pipeline.py`
- Test: `bridge/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Config` (1), `wav.inspect`/`wav.repair_header` (2), `note.NoteData`/`write_note` (3), `postprocess.build`/`PostProcessError` (4), `transcriber.transcribe`/`TranscriptionError` (5)
- Produces: `IncomingNote(note_id, wav_path, meta)`, `resolve_recorded_at(meta, arrived_at) -> tuple[datetime, bool]`, `process_note(incoming, cfg, *, transcribe_fn=..., processor=...) -> Path`

- [ ] **Step 1: Escrever o teste que falha**

```python
# bridge/tests/test_pipeline.py
import struct
from datetime import datetime, timedelta
from pathlib import Path
import pytest

from handy_bridge.config import AsrConfig, Config, PostProcessConfig
from handy_bridge.pipeline import IncomingNote, process_note, resolve_recorded_at
from handy_bridge.postprocess import PostProcessError, PostProcessResult
from handy_bridge.transcriber import Transcription, TranscriptionError

ARRIVED = datetime(2026, 9, 7, 15, 0, 0)


def make_wav(path: Path, seconds: int = 1) -> Path:
    frames = 16000 * seconds
    data = b"\x00\x01" * frames
    path.write_bytes(
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )
    return path


def make_cfg(tmp_path, *, pp_enabled=True) -> Config:
    vault = tmp_path / "Reading"
    vault.mkdir(exist_ok=True)
    return Config(
        vault_path=vault, inbox_folder="Inbox", audio_store=tmp_path / "audio", port=8787,
        asr=AsrConfig(handy_exe=tmp_path / "handy.exe", model="m.gguf", timeout_s=900),
        post_process=PostProcessConfig(enabled=pp_enabled, backend="claude_cli", model="haiku"),
    )


class FakeProcessor:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def process(self, transcript):
        if self.error:
            raise self.error
        return self.result


def ok_transcribe(text="ola mundo"):
    return lambda wav, cfg: Transcription(text=text, audio_secs=1.0, rtf=1.3, model="m.gguf")


# --- clock reconstruction -------------------------------------------------

def test_uses_device_clock_when_synced():
    meta = {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == datetime(2026, 9, 7, 14, 32, 11)
    assert estimated is False


def test_reconstructs_time_when_clock_not_synced():
    # gravado 5 minutos de uptime antes do upload
    meta = {"clock_synced": False, "uptime_ms": 60_000, "uptime_at_upload_ms": 360_000}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == ARRIVED - timedelta(milliseconds=300_000)
    assert estimated is True


def test_falls_back_to_arrival_when_uptime_missing():
    when, estimated = resolve_recorded_at({"clock_synced": False}, ARRIVED)
    assert when == ARRIVED
    assert estimated is True


def test_falls_back_to_arrival_when_recorded_at_unparseable():
    meta = {"clock_synced": True, "recorded_at": "not-a-date"}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == ARRIVED
    assert estimated is True


# --- orchestration --------------------------------------------------------

def test_writes_note_with_post_processed_fields(tmp_path):
    cfg = make_cfg(tmp_path)
    wav = make_wav(tmp_path / "n.wav")
    incoming = IncomingNote("n", wav, {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"})
    processor = FakeProcessor(PostProcessResult("Meu titulo", ["ideia"], "Texto limpo."))

    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=processor)
    text = path.read_text(encoding="utf-8")

    assert path.name == "2026-09-07 1432 - Meu titulo.md"
    assert "Texto limpo." in text
    assert "> ola mundo" in text          # transcrição crua preservada
    assert "tags: [ideia]" in text


def test_degrades_to_raw_transcript_when_post_processing_fails(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote("n", make_wav(tmp_path / "n.wav"),
                            {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"})
    processor = FakeProcessor(error=PostProcessError("boom"))

    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=processor)
    text = path.read_text(encoding="utf-8")

    assert path.exists()
    assert "ola mundo" in text
    assert "2026-09-07 1432" in path.name


def test_works_with_post_processing_disabled(tmp_path):
    cfg = make_cfg(tmp_path, pp_enabled=False)
    incoming = IncomingNote("n", make_wav(tmp_path / "n.wav"),
                            {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"})
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)
    assert "ola mundo" in path.read_text(encoding="utf-8")


def test_empty_transcription_still_writes_a_note(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote("n", make_wav(tmp_path / "n.wav"),
                            {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"})
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(""), processor=None)
    assert path.exists()
    assert "(transcrição vazia)" in path.read_text(encoding="utf-8")


def test_repairs_truncated_wav_before_transcribing(tmp_path):
    cfg = make_cfg(tmp_path)
    wav = make_wav(tmp_path / "n.wav")
    # zera os tamanhos, simulando queda de energia durante a gravação
    with wav.open("r+b") as fh:
        fh.seek(4); fh.write(struct.pack("<I", 0))
        fh.seek(40); fh.write(struct.pack("<I", 0))

    incoming = IncomingNote("n", wav, {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"})
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)

    assert path.exists()
    assert struct.unpack_from("<I", wav.read_bytes(), 40)[0] == 32000


def test_carries_book_anchor_from_meta_into_the_note(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote("n", make_wav(tmp_path / "n.wav"), {
        "clock_synced": True, "recorded_at": "2026-09-07T14:32:11",
        "book": "epdf.pub_sapiens", "word_offset": 12438,
        "excerpt": "a Revolução Agrícola foi a maior fraude da história",
    })
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)
    text = path.read_text(encoding="utf-8")

    assert 'book: "[[epdf.pub_sapiens]]"' in text
    assert "word_offset: 12438" in text
    assert "> [!quote] Trecho que eu estava lendo" in text


def test_standalone_note_has_no_anchor(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote("n", make_wav(tmp_path / "n.wav"),
                            {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"})
    text = process_note(incoming, cfg, transcribe_fn=ok_transcribe(),
                        processor=None).read_text(encoding="utf-8")
    assert "book:" not in text
    assert "[!quote]" not in text


def test_transcription_failure_propagates(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote("n", make_wav(tmp_path / "n.wav"), {"clock_synced": True})

    def boom(wav, asr_cfg):
        raise TranscriptionError("handy exploded")

    with pytest.raises(TranscriptionError):
        process_note(incoming, cfg, transcribe_fn=boom, processor=None)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.pipeline'`

- [ ] **Step 3: Implementar `pipeline.py`**

```python
# bridge/src/handy_bridge/pipeline.py
"""Orchestrate a single note: repair, transcribe, post-process, write."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from handy_bridge import wav as wav_mod
from handy_bridge.config import AsrConfig, Config
from handy_bridge.note import NoteData, write_note
from handy_bridge.postprocess import PostProcessError, PostProcessor
from handy_bridge.transcriber import Transcription, transcribe as default_transcribe

log = logging.getLogger(__name__)

EMPTY_BODY = "(transcrição vazia)"


@dataclass(frozen=True)
class IncomingNote:
    note_id: str
    wav_path: Path
    meta: dict


def resolve_recorded_at(meta: dict, arrived_at: datetime) -> tuple[datetime, bool]:
    """Return (timestamp, was_estimated).

    The board has no battery-backed RTC, so a recording made offline after a reboot
    carries no wall-clock time. In that case the device sends monotonic uptime and
    the bridge reconstructs the moment from when the upload arrived.
    """
    if meta.get("clock_synced"):
        raw = meta.get("recorded_at")
        if raw:
            try:
                return datetime.fromisoformat(str(raw)), False
            except ValueError:
                log.warning("unparseable recorded_at %r; falling back to arrival", raw)
        return arrived_at, True

    recorded_uptime = meta.get("uptime_ms")
    upload_uptime = meta.get("uptime_at_upload_ms")
    if recorded_uptime is not None and upload_uptime is not None:
        delta_ms = float(upload_uptime) - float(recorded_uptime)
        if delta_ms >= 0:
            return arrived_at - timedelta(milliseconds=delta_ms), True
    return arrived_at, True


def process_note(
    incoming: IncomingNote,
    cfg: Config,
    *,
    transcribe_fn: Callable[[Path, AsrConfig], Transcription] = default_transcribe,
    processor: PostProcessor | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> Path:
    arrived_at = now()

    try:
        wav_mod.repair_header(incoming.wav_path)
    except wav_mod.InvalidWav:
        log.warning("could not repair %s; transcribing as-is", incoming.wav_path)
    info = wav_mod.inspect(incoming.wav_path)

    transcription = transcribe_fn(incoming.wav_path, cfg.asr)
    raw_text = transcription.text.strip()

    recorded_at, estimated = resolve_recorded_at(incoming.meta, arrived_at)
    title = f"{recorded_at:%Y-%m-%d %H%M}"
    tags: list[str] = []
    body = raw_text or EMPTY_BODY

    if processor is not None and raw_text:
        try:
            result = processor.process(raw_text)
            title = result.title or title
            tags = result.tags
            body = result.cleaned or raw_text
        except PostProcessError as exc:
            # A post-processing failure must never cost a note.
            log.warning("post-processing failed for %s: %s", incoming.note_id, exc)

    word_offset = incoming.meta.get("word_offset")
    return write_note(
        cfg.inbox_path,
        NoteData(
            title=title,
            tags=tags,
            body=body,
            raw_transcript=raw_text,
            recorded_at=recorded_at,
            date_estimated=estimated,
            duration_s=info.duration_s,
            asr_model=transcription.model,
            # Anchor fields arrive only when the device recorded from the reader.
            book=incoming.meta.get("book") or None,
            word_offset=int(word_offset) if word_offset is not None else None,
            excerpt=incoming.meta.get("excerpt") or None,
        ),
    )
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd bridge && uv run pytest tests/test_pipeline.py -v`
Expected: PASS (12 testes)

- [ ] **Step 5: Rodar a suíte inteira**

Run: `cd bridge && uv run pytest -v`
Expected: PASS (todos os testes das tasks 1-6)

- [ ] **Step 6: Commit**

```bash
git add bridge/src/handy_bridge/pipeline.py bridge/tests/test_pipeline.py
git commit -m "feat(bridge): orchestrate note pipeline with clock reconstruction"
```

---

### Task 7: Servidor HTTP `POST /v1/notes`

O device não pode ficar com a rádio ligada esperando inferência. O servidor persiste o
WAV, responde `200` na hora, e transcreve em background.

**Files:**
- Create: `bridge/src/handy_bridge/server.py`
- Test: `bridge/tests/test_server.py`

**Interfaces:**
- Consumes: `Config` (1), `IncomingNote`/`process_note` (6)
- Produces: `create_app(cfg: Config, submit: Callable[[IncomingNote], None] | None = None) -> FastAPI`

- [ ] **Step 1: Escrever o teste que falha**

```python
# bridge/tests/test_server.py
import json
import struct
from pathlib import Path
from fastapi.testclient import TestClient

from handy_bridge.config import AsrConfig, Config, PostProcessConfig
from handy_bridge.server import create_app


def make_cfg(tmp_path) -> Config:
    vault = tmp_path / "Reading"
    vault.mkdir(exist_ok=True)
    return Config(
        vault_path=vault, inbox_folder="Inbox", audio_store=tmp_path / "audio", port=8787,
        asr=AsrConfig(handy_exe=tmp_path / "handy.exe", model="m.gguf", timeout_s=900),
        post_process=PostProcessConfig(enabled=False, backend="none", model=""),
    )


def wav_bytes(seconds: int = 1) -> bytes:
    data = b"\x00\x01" * (16000 * seconds)
    return (
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )


def client(tmp_path, submitted=None):
    cfg = make_cfg(tmp_path)
    cfg.audio_store.mkdir(parents=True, exist_ok=True)
    submit = submitted.append if submitted is not None else (lambda incoming: None)
    return TestClient(create_app(cfg, submit=submit)), cfg


def post_note(c, *, audio=None, meta=None):
    meta = {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"} if meta is None else meta
    return c.post(
        "/v1/notes",
        files={"audio": ("20260907-143211.wav", audio or wav_bytes(), "audio/wav")},
        data={"meta": json.dumps(meta)},
    )


def test_accepts_note_and_returns_id(tmp_path):
    c, _ = client(tmp_path)
    resp = post_note(c)
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert resp.json()["id"] == "20260907-143211"


def test_persists_wav_to_audio_store(tmp_path):
    c, cfg = client(tmp_path)
    post_note(c)
    stored = list(cfg.audio_store.glob("*.wav"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == wav_bytes()


def test_hands_incoming_note_to_the_worker(tmp_path):
    submitted = []
    c, _ = client(tmp_path, submitted)
    post_note(c)
    assert len(submitted) == 1
    assert submitted[0].note_id == "20260907-143211"
    assert submitted[0].meta["clock_synced"] is True


def test_rejects_malformed_meta_json(tmp_path):
    c, _ = client(tmp_path)
    resp = c.post(
        "/v1/notes",
        files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
        data={"meta": "{not json"},
    )
    assert resp.status_code == 400
    assert "meta" in resp.json()["error"]


def test_rejects_audio_too_short_to_be_speech(tmp_path):
    c, _ = client(tmp_path)
    tiny = wav_bytes()[:44] + b"\x00" * 100  # bem menos de 1 s
    resp = post_note(c, audio=tiny)
    assert resp.status_code == 400
    assert "short" in resp.json()["error"]


def test_rejects_non_wav_payload(tmp_path):
    c, _ = client(tmp_path)
    resp = post_note(c, audio=b"NOPE" + b"\x00" * 200)
    assert resp.status_code == 400


def test_health_endpoint(tmp_path):
    c, _ = client(tmp_path)
    assert c.get("/v1/health").status_code == 200
    assert c.get("/v1/health").json()["status"] == "ok"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.server'`

- [ ] **Step 3: Implementar `server.py`**

```python
# bridge/src/handy_bridge/server.py
"""HTTP surface: accept a recording, persist it, acknowledge immediately."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import JSONResponse

from handy_bridge import wav as wav_mod
from handy_bridge.config import Config
from handy_bridge.pipeline import IncomingNote

log = logging.getLogger(__name__)

MIN_AUDIO_SECONDS = 1.0


def create_app(cfg: Config, submit: Callable[[IncomingNote], None] | None = None) -> FastAPI:
    app = FastAPI(title="handy-bridge")
    hand_off = submit or (lambda incoming: None)

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/notes")
    async def receive_note(audio: UploadFile, meta: str = Form(...)) -> JSONResponse:
        try:
            parsed_meta = json.loads(meta)
        except json.JSONDecodeError:
            return JSONResponse({"error": "meta is not valid JSON"}, status_code=400)

        note_id = Path(audio.filename or "note").stem
        cfg.audio_store.mkdir(parents=True, exist_ok=True)
        target = cfg.audio_store / f"{note_id}.wav"
        target.write_bytes(await audio.read())

        try:
            info = wav_mod.inspect(target)
        except wav_mod.InvalidWav as exc:
            target.unlink(missing_ok=True)
            return JSONResponse({"error": f"invalid WAV: {exc}"}, status_code=400)

        if info.duration_s < MIN_AUDIO_SECONDS:
            target.unlink(missing_ok=True)
            return JSONResponse(
                {"error": f"audio too short: {info.duration_s:.2f}s"}, status_code=400
            )

        # Acknowledge as soon as the audio is safely on disk; transcription is async
        # so the device can drop its radio instead of waiting on inference.
        hand_off(IncomingNote(note_id=note_id, wav_path=target, meta=parsed_meta))
        log.info("accepted note %s (%.1fs)", note_id, info.duration_s)
        return JSONResponse({"id": note_id, "status": "accepted"}, status_code=200)

    return app
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd bridge && uv run pytest tests/test_server.py -v`
Expected: PASS (7 testes)

- [ ] **Step 5: Commit**

```bash
git add bridge/src/handy_bridge/server.py bridge/tests/test_server.py
git commit -m "feat(bridge): accept recordings over HTTP and ack immediately"
```

---

### Task 8: Fila em background, anúncio mDNS e entrypoint

**Files:**
- Create: `bridge/src/handy_bridge/discovery.py`
- Create: `bridge/src/handy_bridge/worker.py`
- Create: `bridge/src/handy_bridge/__main__.py`
- Test: `bridge/tests/test_worker.py`

**Interfaces:**
- Consumes: `Config` (1), `IncomingNote`/`process_note` (6), `create_app` (7), `postprocess.build` (4)
- Produces: `NoteWorker(cfg, processor, transcribe_fn=...)` com `.start()`, `.submit(incoming)`, `.stop(timeout=30.0)`; `advertise(port, name="handy-bridge") -> tuple[Zeroconf, ServiceInfo]` (o `Zeroconf` precisa ser fechado no encerramento); `SERVICE_TYPE = "_handybridge._tcp.local."`; `main(argv=None) -> int`

- [ ] **Step 1: Escrever o teste que falha**

```python
# bridge/tests/test_worker.py
import struct
from pathlib import Path
from handy_bridge.config import AsrConfig, Config, PostProcessConfig
from handy_bridge.pipeline import IncomingNote
from handy_bridge.transcriber import Transcription, TranscriptionError
from handy_bridge.worker import NoteWorker


def make_cfg(tmp_path) -> Config:
    vault = tmp_path / "Reading"
    vault.mkdir(exist_ok=True)
    return Config(
        vault_path=vault, inbox_folder="Inbox", audio_store=tmp_path / "audio", port=8787,
        asr=AsrConfig(handy_exe=tmp_path / "handy.exe", model="m.gguf", timeout_s=900),
        post_process=PostProcessConfig(enabled=False, backend="none", model=""),
    )


def make_wav(path: Path) -> Path:
    data = b"\x00\x01" * 16000
    path.write_bytes(
        b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data" + struct.pack("<I", len(data)) + data
    )
    return path


def test_processes_submitted_notes(tmp_path):
    cfg = make_cfg(tmp_path)
    worker = NoteWorker(
        cfg, processor=None,
        transcribe_fn=lambda wav, c: Transcription("ola", 1.0, 1.3, "m.gguf"),
    )
    worker.start()
    worker.submit(IncomingNote("n1", make_wav(tmp_path / "n1.wav"),
                               {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}))
    worker.stop(timeout=10)

    notes = list((cfg.inbox_path).glob("*.md"))
    assert len(notes) == 1
    assert "ola" in notes[0].read_text(encoding="utf-8")


def test_one_failure_does_not_kill_the_worker(tmp_path):
    cfg = make_cfg(tmp_path)
    calls = {"n": 0}

    def flaky(wav, c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TranscriptionError("first one explodes")
        return Transcription("segunda", 1.0, 1.3, "m.gguf")

    worker = NoteWorker(cfg, processor=None, transcribe_fn=flaky)
    worker.start()
    worker.submit(IncomingNote("n1", make_wav(tmp_path / "n1.wav"), {"clock_synced": False}))
    worker.submit(IncomingNote("n2", make_wav(tmp_path / "n2.wav"), {"clock_synced": False}))
    worker.stop(timeout=10)

    notes = list((cfg.inbox_path).glob("*.md"))
    assert len(notes) == 1
    assert "segunda" in notes[0].read_text(encoding="utf-8")
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_worker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.worker'`

- [ ] **Step 3: Implementar `worker.py`**

```python
# bridge/src/handy_bridge/worker.py
"""Serialize note processing on a single background thread.

Transcription is GPU-bound and the device may upload a burst after being offline,
so notes are handled one at a time rather than concurrently.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from handy_bridge.config import AsrConfig, Config
from handy_bridge.pipeline import IncomingNote, process_note
from handy_bridge.postprocess import PostProcessor
from handy_bridge.transcriber import Transcription, transcribe as default_transcribe

log = logging.getLogger(__name__)

_SHUTDOWN = object()


class NoteWorker:
    def __init__(
        self,
        cfg: Config,
        processor: PostProcessor | None,
        transcribe_fn: Callable[..., Transcription] = default_transcribe,
    ):
        self._cfg = cfg
        self._processor = processor
        self._transcribe_fn = transcribe_fn
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="note-worker", daemon=True)
        self._thread.start()

    def submit(self, incoming: IncomingNote) -> None:
        self._queue.put(incoming)

    def stop(self, timeout: float = 30.0) -> None:
        self._queue.put(_SHUTDOWN)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SHUTDOWN:
                return
            try:
                path = process_note(
                    item, self._cfg,
                    transcribe_fn=self._transcribe_fn,
                    processor=self._processor,
                )
                log.info("wrote note %s -> %s", item.note_id, path.name)
            except Exception:
                # One bad recording must not take the worker down with it.
                log.exception("failed to process note %s", item.note_id)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd bridge && uv run pytest tests/test_worker.py -v`
Expected: PASS (2 testes)

- [ ] **Step 5: Implementar `discovery.py`**

```python
# bridge/src/handy_bridge/discovery.py
"""Advertise the bridge on the LAN so the device finds it without a configured IP."""
from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo, Zeroconf

SERVICE_TYPE = "_handybridge._tcp.local."
log = logging.getLogger(__name__)


def _primary_ipv4() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))  # no packet is sent; picks the default route
        return sock.getsockname()[0]
    finally:
        sock.close()


def advertise(port: int, name: str = "handy-bridge") -> tuple[Zeroconf, ServiceInfo]:
    address = _primary_ipv4()
    info = ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        addresses=[socket.inet_aton(address)],
        port=port,
        properties={"version": "1", "path": "/v1/notes"},
        server=f"{name}.local.",
    )
    zc = Zeroconf()
    zc.register_service(info)
    log.info("advertising %s at %s:%d", SERVICE_TYPE, address, port)
    return zc, info
```

- [ ] **Step 6: Implementar `__main__.py`**

```python
# bridge/src/handy_bridge/__main__.py
"""Entrypoint: wire config, worker, mDNS and the HTTP server together."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from handy_bridge.config import ConfigError, load_config
from handy_bridge.discovery import advertise
from handy_bridge.postprocess import build as build_processor
from handy_bridge.server import create_app
from handy_bridge.worker import NoteWorker


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="handy-bridge")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
        processor = build_processor(cfg.post_process)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    worker = NoteWorker(cfg, processor)
    worker.start()

    zc = None
    try:
        zc, _ = advertise(cfg.port)
    except OSError:
        logging.getLogger(__name__).warning(
            "mDNS advertisement failed; the device will need a configured address"
        )

    app = create_app(cfg, submit=worker.submit)
    try:
        uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_config=None)
    finally:
        if zc is not None:
            zc.close()
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Rodar a suíte inteira**

Run: `cd bridge && uv run pytest -v`
Expected: PASS (todos os testes das tasks 1-8)

- [ ] **Step 8: Commit**

```bash
git add bridge/src/handy_bridge/worker.py bridge/src/handy_bridge/discovery.py bridge/src/handy_bridge/__main__.py bridge/tests/test_worker.py
git commit -m "feat(bridge): background worker, mDNS advertisement and entrypoint"
```

---

### Task 9: Verificação de ponta a ponta com áudio real

Até aqui tudo foi testado com dublês. Esta task prova que o serviço funciona contra o
Handy e o `claude` de verdade, e mede a latência real com fala em português.

**Files:**
- Create: `bridge/README.md`
- Create: `bridge/config.toml` (local, não versionado)
- Modify: `bridge/.gitignore`

- [ ] **Step 1: Ignorar o config local e o áudio**

```bash
cd bridge
printf 'config.toml\n.venv/\n__pycache__/\n*.wav\n' >> .gitignore
cp config.example.toml config.toml
```

- [ ] **Step 2: Subir o serviço**

```bash
cd bridge && uv run python -m handy_bridge --config config.toml --log-level info
```

Expected: log `advertising _handybridge._tcp.local. at <ip>:8787` e o uvicorn escutando na 8787.

- [ ] **Step 3: Gravar um áudio de teste em português**

Grave ~20 s falando naturalmente (celular ou o próprio Handy) e converta para o formato que o device produz:

```bash
ffmpeg -i entrada.m4a -ar 16000 -ac 1 -c:a pcm_s16le teste.wav
```

Se não houver `ffmpeg`, use o Audacity exportando como WAV PCM 16-bit, 16000 Hz, mono.

- [ ] **Step 4: Enviar como o device enviaria**

```bash
curl -X POST http://localhost:8787/v1/notes \
  -F "audio=@teste.wav;type=audio/wav;filename=20260907-143211.wav" \
  -F 'meta={"clock_synced":true,"recorded_at":"2026-09-07T14:32:11","device_id":"rsvp-nano","board_id":"waveshare_esp32s3_touch_lcd_3_49_rev2","firmware_version":"dev"}'
```

Expected: `{"id":"20260907-143211","status":"accepted"}` retornando em menos de 1 s.

- [ ] **Step 5: Conferir a nota no vault**

```bash
ls -la "C:/Users/kakam/OneDrive/Área de Trabalho/Reading/Inbox/"
cat "C:/Users/kakam/OneDrive/Área de Trabalho/Reading/Inbox/"*.md
```

Verificar, um a um:
- o arquivo existe e o nome começa com `2026-09-07 1432 - `
- o frontmatter tem `title`, `date`, `duration`, `source: rsvp-nano`, `asr_model`, `tags`
- o corpo tem o texto limpo
- o callout `> [!note]- Transcrição original` tem a transcrição crua
- **nenhum arquivo `.partial` sobrou** na pasta
- o OneDrive não criou cópia de conflito

- [ ] **Step 6: Medir a latência real e registrar**

Nos logs do serviço, anotar o tempo entre `accepted note` e `wrote note`. Comparar com
a estimativa da spec (~0,75× a duração do áudio + ~1,4 s de carga + ~5 s do `claude`).

- [ ] **Step 7: Testar a degradação sem LLM**

Editar `config.toml` com `enabled = false` em `[post_process]`, reiniciar e repetir os
passos 4-5. Expected: a nota é escrita com a transcrição crua e título por timestamp.

- [ ] **Step 8: Testar rejeição de áudio curto**

```bash
curl -X POST http://localhost:8787/v1/notes \
  -F "audio=@teste.wav;type=audio/wav;filename=curto.wav" \
  -F 'meta={"clock_synced":false}' --max-time 30
```

Com um WAV de menos de 1 s. Expected: HTTP 400 com `"audio too short"`.

- [ ] **Step 9: Escrever o `README.md` do bridge**

Documentar: o que é, como instalar (`uv sync`), como configurar (copiar
`config.example.toml`), como rodar, como rodar os testes, a latência medida no passo 6,
e como trocar o backend de pós-processamento.

- [ ] **Step 10: Commit**

```bash
git add bridge/README.md bridge/.gitignore
git commit -m "docs(bridge): document setup and record measured end-to-end latency"
```

---

### Task 10: Conversão do epub para markdown no vault

Dá ao Claudian o livro inteiro como texto pesquisável (D13 na spec), para perguntas que
o trecho embutido na nota não alcança. Feito com biblioteca padrão — um epub é um zip
cuja ordem de leitura está declarada no OPF, então não precisamos de dependência nova.

**Files:**
- Create: `bridge/src/handy_bridge/epub.py`
- Modify: `bridge/src/handy_bridge/pipeline.py` (chamada best-effort quando a nota tem `book`)
- Test: `bridge/tests/test_epub.py`

**Interfaces:**
- Consumes: nada de tasks anteriores
- Produces: `EpubError`, `convert_to_markdown(epub_path: Path, out_path: Path) -> Path`, `ensure_book_markdown(vault_path: Path, book_stem: str, subfolder: str = "Books") -> Path | None`

- [ ] **Step 1: Escrever o teste que falha**

```python
# bridge/tests/test_epub.py
import zipfile
from pathlib import Path
import pytest
from handy_bridge.epub import convert_to_markdown, ensure_book_markdown, EpubError

CONTAINER = """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf"
    media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>
    <item id="c2" href="zeta.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="alpha.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="s.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="c2"/>
    <itemref idref="c1"/>
  </spine>
</package>"""

CH_ZETA = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <h1>Primeiro Capitulo</h1><p>Texto do primeiro.</p>
  <style>p { color: red }</style>
</body></html>"""

CH_ALPHA = """<html xmlns="http://www.w3.org/1999/xhtml"><body>
  <h2>Segundo Capitulo</h2><p>Texto do <em>segundo</em>.</p>
</body></html>"""


def make_epub(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/zeta.xhtml", CH_ZETA)
        z.writestr("OEBPS/alpha.xhtml", CH_ALPHA)
        z.writestr("OEBPS/s.css", "p{}")
    return path


def test_converts_and_respects_spine_order(tmp_path):
    out = convert_to_markdown(make_epub(tmp_path / "b.epub"), tmp_path / "b.md")
    text = out.read_text(encoding="utf-8")
    assert "# Primeiro Capitulo" in text
    assert "## Segundo Capitulo" in text
    # zeta vem antes de alpha porque o spine manda, nao a ordem alfabetica
    assert text.index("Primeiro Capitulo") < text.index("Segundo Capitulo")


def test_keeps_inline_text_and_drops_style_blocks(tmp_path):
    text = convert_to_markdown(make_epub(tmp_path / "b.epub"),
                               tmp_path / "b.md").read_text(encoding="utf-8")
    assert "Texto do segundo." in text
    assert "color: red" not in text


def test_rejects_non_zip(tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip")
    with pytest.raises(EpubError, match="not a valid epub"):
        convert_to_markdown(bad, tmp_path / "out.md")


def test_rejects_epub_without_container(tmp_path):
    p = tmp_path / "empty.epub"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
    with pytest.raises(EpubError, match="container.xml"):
        convert_to_markdown(p, tmp_path / "out.md")


def test_ensure_creates_markdown_under_subfolder(tmp_path):
    make_epub(tmp_path / "sapiens.epub")
    out = ensure_book_markdown(tmp_path, "sapiens")
    assert out is not None
    assert out == tmp_path / "Books" / "sapiens.md"
    assert "Primeiro Capitulo" in out.read_text(encoding="utf-8")


def test_ensure_is_idempotent(tmp_path):
    make_epub(tmp_path / "sapiens.epub")
    first = ensure_book_markdown(tmp_path, "sapiens")
    first.write_text("EDITADO A MAO", encoding="utf-8")
    second = ensure_book_markdown(tmp_path, "sapiens")
    # nao reconverte: o arquivo existente e preservado
    assert second.read_text(encoding="utf-8") == "EDITADO A MAO"


def test_ensure_returns_none_when_no_epub(tmp_path):
    assert ensure_book_markdown(tmp_path, "inexistente") is None
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `cd bridge && uv run pytest tests/test_epub.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'handy_bridge.epub'`

- [ ] **Step 3: Implementar `epub.py`**

```python
# bridge/src/handy_bridge/epub.py
"""Convert an epub to plain markdown so an agent can read the whole book.

An epub is a zip whose reading order is declared in the OPF spine, so this needs
only the standard library: zipfile for the container, ElementTree for the OPF, and
HTMLParser for the chapter bodies.
"""
from __future__ import annotations

import logging
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

log = logging.getLogger(__name__)

CONTAINER_PATH = "META-INF/container.xml"
_SKIP_TAGS = {"style", "script", "head", "title"}
_HEADINGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "blockquote", *_HEADINGS}


class EpubError(Exception):
    """Raised when a file is not a usable epub."""


class _ChapterParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._pending_heading: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _HEADINGS:
            self._parts.append("\n\n")
            self._pending_heading = _HEADINGS[tag]
            self._parts.append(_HEADINGS[tag] + " ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _HEADINGS:
            self._pending_heading = None
            self._parts.append("\n\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._parts.append(data)

    def text(self) -> str:
        joined = "".join(self._parts)
        joined = re.sub(r"[ \t\r\f\v]+", " ", joined)
        joined = re.sub(r" ?\n ?", "\n", joined)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()


def _spine_hrefs(archive: zipfile.ZipFile) -> list[str]:
    try:
        container = archive.read(CONTAINER_PATH)
    except KeyError as exc:
        raise EpubError(f"missing {CONTAINER_PATH}") from exc

    root = ElementTree.fromstring(container)
    rootfile = root.find(".//{*}rootfile")
    if rootfile is None or not rootfile.get("full-path"):
        raise EpubError("container.xml declares no rootfile")
    opf_path = rootfile.get("full-path")

    opf = ElementTree.fromstring(archive.read(opf_path))
    base = posixpath.dirname(opf_path)
    manifest = {
        item.get("id"): item.get("href")
        for item in opf.findall(".//{*}manifest/{*}item")
        if item.get("id") and item.get("href")
    }
    hrefs = []
    for ref in opf.findall(".//{*}spine/{*}itemref"):
        href = manifest.get(ref.get("idref"))
        if href:
            hrefs.append(posixpath.normpath(posixpath.join(base, href)) if base else href)
    return hrefs


def convert_to_markdown(epub_path: Path, out_path: Path) -> Path:
    epub_path, out_path = Path(epub_path), Path(out_path)
    if not zipfile.is_zipfile(epub_path):
        raise EpubError(f"not a valid epub (not a zip): {epub_path}")

    chunks: list[str] = []
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
            if text:
                chunks.append(text)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".partial")
    tmp.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    tmp.replace(out_path)
    return out_path


def ensure_book_markdown(
    vault_path: Path, book_stem: str, subfolder: str = "Books"
) -> Path | None:
    """Convert `<vault>/<stem>.epub` once. Returns the markdown path, or None."""
    vault_path = Path(vault_path)
    target = vault_path / subfolder / f"{book_stem}.md"
    if target.exists():
        return target
    source = vault_path / f"{book_stem}.epub"
    if not source.is_file():
        log.info("no epub found for %r in %s", book_stem, vault_path)
        return None
    log.info("converting %s -> %s", source.name, target)
    return convert_to_markdown(source, target)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `cd bridge && uv run pytest tests/test_epub.py -v`
Expected: PASS (7 testes)

- [ ] **Step 5: Ligar no pipeline, sem poder derrubar a nota**

Em `pipeline.py`, acrescentar o import e o bloco antes do `return write_note(...)`:

```python
from handy_bridge.epub import EpubError, ensure_book_markdown
```

```python
    book = incoming.meta.get("book") or None
    if book:
        try:
            ensure_book_markdown(cfg.vault_path, book)
        except (EpubError, OSError) as exc:
            # Best-effort: the book text is a convenience, never a reason to lose a note.
            log.warning("could not convert book %r: %s", book, exc)
```

E trocar `book=incoming.meta.get("book") or None` por `book=book` na construção do `NoteData`.

- [ ] **Step 6: Rodar a suíte inteira**

Run: `cd bridge && uv run pytest -v`
Expected: PASS — nenhum teste anterior quebra, porque as notas dos testes de pipeline não têm epub no `vault_path` e `ensure_book_markdown` devolve `None`.

- [ ] **Step 7: Verificar com o Sapiens de verdade**

```bash
cd bridge && uv run python -c "
from pathlib import Path
from handy_bridge.epub import ensure_book_markdown
vault = Path('C:/Users/kakam/OneDrive/Área de Trabalho/Reading')
out = ensure_book_markdown(vault, 'epdf.pub_sapiens-uma-breve-historia-da-humanidade')
print(out, out.stat().st_size if out else 0, 'bytes')
"
```

Expected: gera `Reading/Books/epdf.pub_sapiens-uma-breve-historia-da-humanidade.md` com alguns MB de texto. Abrir no Obsidian e conferir que os capítulos estão na ordem certa e legíveis.

- [ ] **Step 8: Commit**

```bash
git add bridge/src/handy_bridge/epub.py bridge/src/handy_bridge/pipeline.py bridge/tests/test_epub.py
git commit -m "feat(bridge): convert epub to markdown for whole-book agent context"
```

---

## Definição de pronto

O plano está completo quando:

1. `cd bridge && uv run pytest` passa inteiro
2. Um WAV real de fala em português vira uma nota no vault `Reading/Inbox/` com título e tags gerados
3. A transcrição crua está preservada na nota
4. Desligar o pós-processamento continua produzindo nota
5. Nenhum arquivo `.partial` sobra e o OneDrive não gera cópia de conflito
6. A latência real está medida e registrada no README
7. Uma nota com `book`/`word_offset`/`excerpt` nos metadados sai com o wikilink e o callout de trecho
8. O Sapiens está convertido em `Reading/Books/*.md`, na ordem correta de capítulos

O próximo plano (firmware) assume este serviço no ar e implementa o lado do device
contra o contrato de `POST /v1/notes` fixado na Task 7.
