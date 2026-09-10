"""Render the derived summaries: one per chapter, one per book.

Both are fed only by what the reader recorded — the cleaned note text, the
question and answer pairs, and the recall corrections. The book's own words never
feed a summary. That is a deliberate constraint, and it has a consequence worth
stating plainly: a summary mirrors what its reader engaged with, not what the
book says, so a chapter read without recording produces nothing. The gap is the
information.

Chapter files are derived and rewritten often, which is safe because they are
reproducible — a OneDrive conflict copy of one costs nothing. The single
exception is `## Minhas observações`, which is read back and preserved verbatim
so a hand-written note inside a generated file survives regeneration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from handy_bridge.chapters import BookIndex
from handy_bridge.layout import KIND_DIRS

SYNTHESIS_HEADING = "## Síntese"
OBSERVATIONS_HEADING = "## Minhas observações"

# The cap is not cosmetic: this section, and only this section, feeds the book
# summary. Without a ceiling the book summary's input grows without bound as the
# reading goes on.
SYNTHESIS_WORD_CAP = 400

# Fixed order, from what was noted through what was asked to what was checked.
SECTION_ORDER = ("Anotações", "Perguntas e respostas", "Recall")

GENERATED_NOTICE = (
    "*Gerado a partir das suas notas. Edições feitas fora de "
    f"`{OBSERVATIONS_HEADING.lstrip('# ')}` são perdidas na próxima gravação.*"
)


def _frontmatter(extra: list[str]) -> list[str]:
    return ["---", "generated: true", *extra, "---", ""]


def read_observations(path: Path) -> str:
    """Read back the reserved section, from its heading to the end of the file.

    To the end, not to the next `##`, and that is the point: the section is last
    by construction, so everything after the heading belongs to the reader —
    including headings that look like generated ones.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""
    at = text.find(OBSERVATIONS_HEADING)
    if at == -1:
        return ""
    return text[at + len(OBSERVATIONS_HEADING) :].strip()


def _cap(text: str) -> str:
    words = text.strip().split()
    if len(words) <= SYNTHESIS_WORD_CAP:
        return text.strip()
    return " ".join(words[:SYNTHESIS_WORD_CAP]) + " […]"


def render_chapter(
    *,
    chapter: int,
    title: str,
    synthesis: str,
    sections: dict[str, list[str]],
    observations: str,
) -> str:
    lines = _frontmatter([f"chapter: {chapter}", f'chapter_title: "{title}"'])
    lines += [f"# {title}", "", GENERATED_NOTICE, ""]
    lines += [SYNTHESIS_HEADING, "", _cap(synthesis) or "(nada registrado ainda)", ""]

    for name in SECTION_ORDER:
        entries = [item.strip() for item in sections.get(name, []) if item.strip()]
        if not entries:
            continue
        lines += [f"## {name}", ""]
        lines += [f"- {entry}" for entry in entries]
        lines.append("")

    # Always last, so preserving it is a matter of reading to the end of the file.
    lines += [OBSERVATIONS_HEADING, "", observations.strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def write_chapter_summary(path: Path, rendered: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(rendered, encoding="utf-8")
    os.replace(tmp, path)
    return path


# --- the book summary ------------------------------------------------------


def read_synthesis(path: Path) -> str:
    """Read only the synthesis section of a chapter summary."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""
    at = text.find(SYNTHESIS_HEADING)
    if at == -1:
        return ""
    rest = text[at + len(SYNTHESIS_HEADING) :]
    end = rest.find("\n## ")
    return (rest if end == -1 else rest[:end]).strip()


def _chapter_number(path: Path) -> int:
    head = path.name.split(" - ", 1)[0].strip()
    return int(head) if head.isdigit() else 0


def _chapter_title(path: Path) -> str:
    parts = path.stem.split(" - ", 1)
    return parts[1] if len(parts) == 2 else path.stem


def collect_syntheses(chapters_dir: Path) -> list[tuple[int, str, str]]:
    """Every chapter's synthesis, in chapter order.

    The book summary is built from these rather than from the raw notes, for two
    reasons: each one is capped, so the input scales with the number of chapters
    instead of the volume of recording; and rebuilding from the whole set is what
    lets the important points be re-ranked when a later chapter changes what
    mattered earlier.
    """
    chapters_dir = Path(chapters_dir)
    if not chapters_dir.is_dir():
        return []
    out = []
    for path in chapters_dir.glob("*.md"):
        synthesis = read_synthesis(path)
        if synthesis:
            out.append((_chapter_number(path), _chapter_title(path), synthesis))
    return sorted(out)


def unrecorded_chapters(
    index: BookIndex, chapters_dir: Path
) -> list[tuple[int, str]]:
    """Chapters with no summary — where the book was read without recording."""
    recorded = {number for number, _, _ in collect_syntheses(chapters_dir)}
    return [(c.index, c.title) for c in index.chapters if c.index not in recorded]


def render_book(
    *,
    title: str,
    bullets: list[str],
    syntheses: list[tuple[int, str, str]],
    unrecorded: list[tuple[int, str]],
) -> str:
    lines = _frontmatter([f'book_title: "{title}"'])
    lines += [f"# {title}", "", GENERATED_NOTICE, ""]

    kept = [item.strip() for item in bullets if item.strip()]
    if kept:
        lines += ["## O que mais importa", ""]
        lines += [f"- {item}" for item in kept]
        lines.append("")

    if syntheses:
        lines += ["## Por capítulo", ""]
        for number, chapter_title, synthesis in syntheses:
            lines += [f"### {number:02d} — {chapter_title}", "", synthesis, ""]

    if unrecorded:
        lines += ["## Capítulos sem registro", ""]
        lines += [f"- {number:02d} — {name}" for number, name in unrecorded]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- reading the notes back -------------------------------------------------


@dataclass(frozen=True)
class NoteSummary:
    """What a chapter summary needs from one note on disk."""

    stem: str
    kind: str
    title: str
    body: str
    questions: list[tuple[str, str]]
    recall: list[str]
    # Reading position, read from the frontmatter. The filename carries the date
    # instead, so these are the only place position survives.
    chapter: int | None = None
    word_offset: int | None = None


def _callout_blocks(lines: list[str], marker: str) -> list[list[str]]:
    """Every callout of one type, as its header plus quoted lines."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith(marker):
            current = [line]
            blocks.append(current)
        elif current is not None and line.startswith(">"):
            current.append(line)
        else:
            current = None
    return blocks


def _field(lines: list[str], name: str) -> str:
    """Read one frontmatter field, stopping at the closing delimiter.

    Stopping matters: without it a `kind:` written inside the body would be read
    as if it were the note's own property.
    """
    prefix = f"{name}:"
    for at, line in enumerate(lines):
        if at > 0 and line.strip() == "---":
            break
        if line.startswith(prefix):
            return line[len(prefix) :].strip().strip('"')
    return ""


def parse_note(path: Path) -> NoteSummary | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    lines = text.splitlines()

    body_start = 0
    if lines and lines[0].strip() == "---":
        for at, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                body_start = at + 1
                break

    body: list[str] = []
    for line in lines[body_start:]:
        if line.startswith(">"):
            break
        body.append(line)

    questions = []
    for block in _callout_blocks(lines, "> [!question]"):
        question = block[0].split("]", 1)[-1].strip()
        answer = " ".join(line.lstrip(">").strip() for line in block[1:]).strip()
        if question:
            questions.append((question, answer))

    recall = []
    for block in _callout_blocks(lines, "> [!success] Conferência"):
        for line in block[1:]:
            cleaned = line.lstrip(">").strip()
            if cleaned.startswith(("✓", "✗", "—")):
                recall.append(cleaned)
            elif cleaned.startswith("**Na verdade:**") and recall:
                recall[-1] = f"{recall[-1]} / {cleaned}"

    return NoteSummary(
        stem=Path(path).stem,
        kind=_field(lines, "kind") or "anotação",
        title=_field(lines, "title") or Path(path).stem,
        body=" ".join(body).strip(),
        questions=questions,
        recall=recall,
        chapter=_int_field(lines, "chapter"),
        word_offset=_int_field(lines, "word_offset"),
    )


def _int_field(lines: list[str], name: str) -> int | None:
    raw = _field(lines, name)
    try:
        return int(raw)
    except ValueError:
        return None


def collect_chapter_notes(book_dir: Path, chapter: int) -> list[NoteSummary]:
    """Every note recorded in one chapter, across the kind directories.

    Selected by the `chapter` in each note's frontmatter, not by its filename: the
    filename carries the recording date now, so it says nothing about position.
    That costs reading every note in the book to build one chapter's summary,
    which is nothing next to the transcription that produced the note.

    Ordered by chapter then word offset, which restores the reading order that
    splitting into kind directories necessarily breaks up.
    """
    book_dir = Path(book_dir)
    found: list[NoteSummary] = []
    for folder in KIND_DIRS.values():
        target = book_dir / folder
        if not target.is_dir():
            continue
        for path in target.glob("*.md"):
            note = parse_note(path)
            if note is not None and note.chapter == chapter:
                found.append(note)
    return sorted(found, key=lambda n: (n.word_offset if n.word_offset is not None else 0, n.stem))


def sections_from(notes: list[NoteSummary]) -> dict[str, list[str]]:
    """Turn the notes into the three sections, without asking a model.

    Only the synthesis is generated. Listing what was noted, asked and checked is
    a matter of quoting the notes, and quoting cannot hallucinate — so it stays
    out of the model's hands and costs nothing.
    """
    # Every kind maps to the section it belongs in, so a note is never invisible
    # in all three. A question that came back unanswered used to be: it added
    # nothing to Anotações (wrong kind) and nothing to Perguntas (no answer
    # callout to read), so only the synthesis ever saw it.
    own_section = {
        "anotação": "Anotações",
        "pergunta": "Perguntas e respostas",
        "recall": "Recall",
    }

    sections: dict[str, list[str]] = {name: [] for name in SECTION_ORDER}
    for note in notes:
        link = f"[[{note.stem}]]"
        for question, answer in note.questions:
            sections["Perguntas e respostas"].append(
                f"**{question}** — {_trim(answer)} {link}"
            )
        for item in note.recall:
            sections["Recall"].append(f"{item} {link}")

        # The note's own words. Skipped only for a question that was answered:
        # there the Q&A pair already carries what was asked, and the fallback in
        # the pipeline can make the question *be* the body, which would print it
        # twice. An annotation's body is the content itself, so it always goes in.
        superseded = note.kind == "pergunta" and bool(note.questions)
        if note.body and not superseded:
            target = own_section.get(note.kind, "Anotações")
            sections[target].append(f"{_trim(note.body)} {link}")
    return sections


def _trim(text: str, limit: int = 240) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def synthesis_input(notes: list[NoteSummary]) -> list[str]:
    """The lines handed to the model to write the synthesis from."""
    out: list[str] = []
    for note in notes:
        if note.body:
            out.append(f"[{note.kind}] {note.body}")
        out += [f"[pergunta] {q} -> {a}" for q, a in note.questions]
        out += [f"[recall] {item}" for item in note.recall]
    return out
