"""Render voice notes as Obsidian Markdown and write them atomically."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from handy_bridge.kind import DEFAULT_KIND, NoteKind

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
    # What the recording was for. Always written: it is the axis the Bases views
    # filter on, so a note without it would be invisible in all three of them.
    kind: NoteKind = DEFAULT_KIND
    # Present only when the recording was triggered from inside the reader.
    book: str | None = None
    word_offset: int | None = None
    excerpt: str | None = None
    # Resolved from the excerpt against the converted book, so these travel with
    # the anchor fields above and are absent for a standalone note.
    chapter: int | None = None
    chapter_title: str | None = None
    chapter_source: str | None = None
    # (question, answer) pairs already resolved for this note. Kept as plain tuples so
    # rendering stays independent of the post-processing package.
    answers: list[tuple[str, str]] = field(default_factory=list)
    # (said, actual, correct) triples from checking a recall against the book, and
    # what the passage covered that went unmentioned. Plain tuples for the same
    # reason as `answers`.
    recall_points: list[tuple[str, str, bool]] = field(default_factory=list)
    recall_missed: list[str] = field(default_factory=list)


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


def normalize_tag(tag: str) -> str:
    """Make a tag usable in Obsidian.

    Obsidian tags accept letters, digits, underscore, hyphen and forward slash —
    never spaces. An LLM happily returns "revolução agrícola", which would render
    as a property value that is not a working tag, so spaces become hyphens.
    Accents are kept: Obsidian handles unicode tags fine.
    """
    cleaned = tag.strip().lstrip("#").strip().lower()
    cleaned = "-".join(cleaned.split())
    return "".join(
        ch for ch in cleaned if ch.isalnum() or ch in "_-/"
    ).strip("-")


def normalize_tags(tags: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for tag in tags:
        normalized = normalize_tag(tag)
        if normalized:
            seen.setdefault(normalized, None)
    return list(seen)


def render(note: NoteData) -> str:
    lines = [
        "---",
        f"title: {_quote(note.title)}",
        # Right after the title so it is the first thing the properties panel shows.
        f"kind: {note.kind}",
        f"date: {note.recorded_at.isoformat(timespec='seconds')}",
        f"duration: {round(note.duration_s)}s",
        "source: rsvp-nano",
        f"asr_model: {note.asr_model}",
        f"tags: [{', '.join(normalize_tags(note.tags))}]",
    ]
    if note.book:
        lines.append(f'book: "[[{note.book}]]"')
    # Offset 0 is the first word of the book, so compare against None explicitly.
    if note.word_offset is not None:
        lines.append(f"word_offset: {note.word_offset}")
    if note.chapter is not None:
        lines.append(f"chapter: {note.chapter}")
        if note.chapter_title:
            lines.append(f"chapter_title: {_quote(note.chapter_title)}")
        # Records whether the chapter came from a text match or a fallback estimate,
        # so a badly placed note stays identifiable without re-running anything.
        if note.chapter_source:
            lines.append(f"chapter_source: {note.chapter_source}")
    if note.date_estimated:
        lines.append("date_estimated: true")
    lines += ["---", "", note.body.strip(), ""]

    excerpt = (note.excerpt or "").strip()
    if excerpt:
        lines.append("> [!quote] Trecho que eu estava lendo")
        lines += [f"> {line}" for line in excerpt.splitlines()]
        lines.append("")

    lines += render_recall(note.recall_points, note.recall_missed)

    # Answers are content the user wants to read, so they render expanded — unlike the
    # raw transcript below, which is reference material and stays collapsed.
    for question, answer in note.answers:
        if not question.strip() or not answer.strip():
            continue
        lines += render_qa(question, answer)

    raw = note.raw_transcript.strip()
    if raw:
        lines.append(RAW_CALLOUT)
        lines += [f"> {line}" for line in raw.splitlines()]
        lines.append("")
    return "\n".join(lines)


RAW_CALLOUT = "> [!note]- Transcrição original"

RECALL_CALLOUT = "> [!success] Conferência do que você lembrou"
RECALL_WARNING = "> *Conferência automática, não verificada.*"


def render_recall(
    points: list[tuple[str, str, bool]], missed: list[str]
) -> list[str]:
    """Show what was said next to what the book says, with equal weight.

    Deliberately not a collapsed callout with only the corrected version on show.
    The vault is self-test material, so the mistake is the part worth finding
    again later — hiding it would optimise for reading and against remembering.
    """
    if not points and not missed:
        return []

    lines = [RECALL_CALLOUT, ">"]
    for said, actual, correct in points:
        if not said.strip():
            continue
        lines.append(f"> {'✓' if correct else '✗'} **Você disse:** {said.strip()}")
        if not correct and actual.strip():
            lines.append(f"> **Na verdade:** {actual.strip()}")
        lines.append(">")
    for item in missed:
        if item.strip():
            lines.append(f"> — **Passou batido:** {item.strip()}")
    if missed:
        lines.append(">")
    # Same warning the answers carry, for the same reason: a correction that
    # arrives on its own is read with less scepticism than one you went looking for.
    lines += [RECALL_WARNING, ""]
    return lines


def render_qa(question: str, answer: str) -> list[str]:
    lines = [f"> [!question] {question.strip()}"]
    lines += [f"> {line}" for line in answer.strip().splitlines()]
    lines.append("")
    return lines


def append_followup(path: Path, question: str, answer: str) -> Path:
    """Add a Telegram follow-up exchange to a note that already exists.

    Inserted just above the raw-transcript callout so every question and answer
    stays together, with the literal transcript remaining last as reference.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")  # raises FileNotFoundError by design
    lines = text.splitlines()

    block = render_qa(question, answer)
    if RAW_CALLOUT in lines:
        at = lines.index(RAW_CALLOUT)
        lines[at:at] = block
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines += block

    tmp = path.with_name(path.name + ".partial")
    tmp.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _unique_path(folder: Path, stem: str) -> Path:
    candidate = folder / f"{stem}.md"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}-{counter}.md"
        counter += 1
    return candidate


def write_note(notes_dir: Path, note: NoteData, *, width: int = 2) -> Path:
    """Write the note atomically so OneDrive never syncs a partial file."""
    # Imported here because `layout` takes `slugify` from this module. The other way
    # out would be a third module holding one function, which buys nothing.
    from handy_bridge.layout import note_stem

    notes_dir = Path(notes_dir)
    notes_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_path(
        notes_dir, note_stem(note.chapter, note.word_offset, note.title, width)
    )

    tmp = target.with_name(target.name + ".partial")
    tmp.write_text(render(note), encoding="utf-8")
    os.replace(tmp, target)
    return target
