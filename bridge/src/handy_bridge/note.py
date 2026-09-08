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
    cleaned = "".join(ch for ch in cleaned if unicodedata.category(ch)[0] != "C")
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
