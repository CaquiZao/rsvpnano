"""Move a vault from the flat Inbox layout to one directory per book.

Idempotent by construction: every step checks whether it already happened, so a
second run reports no moves and touches nothing. `--dry-run` is the default,
because this moves the book's epub inside a OneDrive folder and seeing the plan
first is cheap.

Nothing here deletes a file it did not understand. The old directories are
removed only once they are empty, and whatever is left behind gets reported.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from handy_bridge import bases, chapters as chapters_mod, layout
from handy_bridge.epub import EpubError, convert_to_markdown, epub_source
from handy_bridge.kind import DEFAULT_KIND, spoken_recall_marker
from handy_bridge.note import slugify

log = logging.getLogger(__name__)

LEGACY_INBOX = "Inbox"
LEGACY_BOARDS = "Quadros"
LEGACY_BOOKS = "Books"

QUOTE_CALLOUT = "> [!quote]"
QUESTION_CALLOUT = "> [!question]"
RAW_CALLOUT = "> [!note]-"
SETTINGS_MARKER = "%% kanban:settings"

_OFFSET = re.compile(r"^word_offset:\s*(\d+)\s*$", re.MULTILINE)
_TITLE = re.compile(r'^title:\s*"?(?P<title>.*?)"?\s*$', re.MULTILINE)
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_CARD = re.compile(r"^- \[(?P<mark>[ xX])\]\s*(?P<body>.*)$")


@dataclass(frozen=True)
class Move:
    src: Path
    dst: Path
    why: str

    def __str__(self) -> str:
        return f"{self.why}: {self.src.name} -> {self.dst}"


# --- reading the old notes --------------------------------------------------


def read_excerpt(text: str) -> str | None:
    """Pull the book text back out of the note's quote callout."""
    lines = text.splitlines()
    try:
        at = next(i for i, line in enumerate(lines) if line.startswith(QUOTE_CALLOUT))
    except StopIteration:
        return None
    out = []
    for line in lines[at + 1 :]:
        if not line.startswith(">"):
            break
        out.append(line.lstrip(">").strip())
    return " ".join(out).strip() or None


def read_raw_transcript(text: str) -> str:
    """Read the preserved literal transcript, so the spoken override still works."""
    lines = text.splitlines()
    try:
        at = next(i for i, line in enumerate(lines) if line.startswith(RAW_CALLOUT))
    except StopIteration:
        return ""
    out = []
    for line in lines[at + 1 :]:
        if not line.startswith(">"):
            break
        out.append(line.lstrip(">").strip())
    return " ".join(out).strip()


def classify(text: str) -> str:
    """Work out an old note's kind from what is already written in it.

    Deliberately not an LLM call. A note carrying answered `[!question]` callouts
    was already judged to hold an answerable question by the bridge at the time it
    was written, so reading that back is recovering a decision, not guessing at one.
    """
    if spoken_recall_marker(read_raw_transcript(text)):
        return "recall"
    if QUESTION_CALLOUT in text:
        return "pergunta"
    return DEFAULT_KIND


def read_title(text: str, fallback: str) -> str:
    match = _TITLE.search(text)
    return (match.group("title").strip() if match else "") or fallback


def read_offset(text: str) -> int | None:
    match = _OFFSET.search(text)
    return int(match.group(1)) if match else None


def add_frontmatter(text: str, additions: list[str]) -> str:
    """Insert lines at the end of the frontmatter block, skipping any already there."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    try:
        closing = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        return text

    present = {line.split(":", 1)[0].strip() for line in lines[1:closing] if ":" in line}
    fresh = [
        line for line in additions if line.split(":", 1)[0].strip() not in present
    ]
    if not fresh:
        return text
    lines[closing:closing] = fresh
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


# --- the Kanban board ------------------------------------------------------


def _card_key(body: str) -> str:
    """A card's identity is its text, not the note it happens to link to."""
    return _WIKILINK.sub("", body).strip().casefold()


def dedupe_cards(lines: list[str]) -> list[str]:
    """Collapse cards repeated inside one lane, preferring a checked one.

    Two recordings about the same thing produced the same card twice, once ticked
    and once not. Losing the ticked one reopens work already done; losing the
    unticked one costs a click, so the checked card wins.
    """
    best: dict[str, int] = {}
    out: list[str] = []
    for line in lines:
        match = _CARD.match(line)
        if match is None:
            out.append(line)
            continue
        key = _card_key(match.group("body"))
        checked = match.group("mark").lower() == "x"
        if key not in best:
            best[key] = len(out)
            out.append(line)
            continue
        at = best[key]
        was_checked = (_CARD.match(out[at]) or match).group("mark").lower() == "x"
        if checked and not was_checked:
            out[at] = line
    return out


def rewrite_board(text: str, renames: dict[str, str]) -> str:
    """Point every card at its note's new name, then drop the duplicates.

    Everything below the settings marker is left byte for byte alone: the kanban
    plugin owns that block.
    """
    lines = text.splitlines()
    try:
        stop = next(i for i, line in enumerate(lines) if line.startswith(SETTINGS_MARKER))
    except StopIteration:
        stop = len(lines)

    def relink(line: str) -> str:
        return _WIKILINK.sub(
            lambda m: f"[[{renames.get(m.group(1), m.group(1))}]]", line
        )

    body = dedupe_cards([relink(line) for line in lines[:stop]])
    return "\n".join(body + lines[stop:]).rstrip() + "\n"


# --- the migration itself --------------------------------------------------


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _legacy_books(vault_path: Path) -> list[str]:
    """Book stems that still have something in the old layout."""
    inbox = vault_path / LEGACY_INBOX
    stems = {p.name for p in inbox.iterdir() if p.is_dir()} if inbox.is_dir() else set()
    boards = vault_path / LEGACY_BOARDS
    if boards.is_dir():
        stems |= {p.stem for p in boards.glob("*.md")}
    return sorted(stems)


def _unique(folder: Path, stem: str, taken: set[Path]) -> Path:
    candidate = folder / f"{stem}.md"
    counter = 2
    while candidate.exists() or candidate in taken:
        candidate = folder / f"{stem}-{counter}.md"
        counter += 1
    return candidate


def _migrate_notes(
    vault_path: Path, stem: str, *, dry_run: bool
) -> tuple[list[Move], dict[str, str]]:
    """Move and rewrite one book's notes. Returns the moves and the rename map."""
    source = vault_path / LEGACY_INBOX / stem
    if not source.is_dir():
        return [], {}

    index = chapters_mod.load_index(
        layout.source_dir(vault_path, stem),
        stem,
        epub_source(vault_path, stem),
        # A dry run must not leave a cache file behind.
        write_cache=not dry_run,
    )
    width = index.width if index else layout.MIN_CHAPTER_DIGITS
    target_dir = layout.notes_dir(vault_path, stem)

    moves: list[Move] = []
    renames: dict[str, str] = {}
    taken: set[Path] = set()
    for note in sorted(source.glob("*.md")):
        text = note.read_text(encoding="utf-8")
        excerpt, offset = read_excerpt(text), read_offset(text)
        resolved = chapters_mod.resolve(index, excerpt, offset) if index else None

        additions = [f"kind: {classify(text)}"]
        if resolved is not None:
            additions += [
                f"chapter: {resolved.chapter}",
                f'chapter_title: "{resolved.title}"',
                f"chapter_source: {resolved.source}",
            ]

        title = read_title(text, note.stem)
        new_stem = layout.note_stem(
            resolved.chapter if resolved else None, offset, title, width
        )
        target = _unique(target_dir, new_stem, taken)
        taken.add(target)
        renames[note.stem] = target.stem
        moves.append(Move(note, target, "nota"))

        if not dry_run:
            _write_atomic(target, add_frontmatter(text, additions))
            note.unlink()
    return moves, renames


def _migrate_board(
    vault_path: Path, stem: str, renames: dict[str, str], *, dry_run: bool
) -> list[Move]:
    source = vault_path / LEGACY_BOARDS / f"{stem}.md"
    if not source.is_file():
        return []
    target = layout.board_path(vault_path, stem)
    if not dry_run:
        _write_atomic(target, rewrite_board(source.read_text(encoding="utf-8"), renames))
        source.unlink()
    return [Move(source, target, "quadro")]


def _migrate_sources(vault_path: Path, stem: str, *, dry_run: bool) -> list[Move]:
    """Gather the epub and its converted text into the book's own fonte/."""
    target_dir = layout.source_dir(vault_path, stem)
    moves: list[Move] = []

    epub = epub_source(vault_path, stem)
    if epub.is_file() and epub.parent != target_dir:
        moves.append(Move(epub, target_dir / epub.name, "epub"))
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(epub), str(target_dir / epub.name))

    converted = vault_path / LEGACY_BOOKS / f"{stem}.md"
    target = target_dir / f"{stem}.md"
    if converted.is_file():
        moves.append(Move(converted, target, "texto"))
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(converted), str(target))
    elif not dry_run and not target.exists():
        # No converted text yet: produce it now, so the book is searchable and the
        # chapter index has something to rebuild from.
        moved_epub = target_dir / f"{stem}.epub"
        if moved_epub.is_file():
            try:
                convert_to_markdown(moved_epub, target)
            except (EpubError, OSError) as exc:
                log.warning("could not convert %s: %s", moved_epub.name, exc)
    return moves


def _orphan_epubs(
    vault_path: Path, already: set[Path], *, dry_run: bool
) -> list[Move]:
    """Give a book directory to an epub nobody has recorded against yet."""
    moves: list[Move] = []
    candidates = sorted(vault_path.glob("*.epub")) + sorted(
        (vault_path / LEGACY_BOOKS).glob("*.epub")
    )
    for epub in candidates:
        # A book with notes already had its epub moved by _migrate_sources. During a
        # dry run that file is still on disk, so the planned moves are what says so.
        if epub in already:
            continue
        target = layout.source_dir(vault_path, epub.stem) / epub.name
        moves.append(Move(epub, target, "epub sem notas"))
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(epub), str(target))
    return moves


def _drop_empty_legacy_dirs(
    vault_path: Path, moved: set[Path], *, dry_run: bool
) -> list[str]:
    """Remove the old directories once empty; report anything left behind.

    `moved` carries the sources of every planned move, so a dry run judges the
    layout it would produce rather than the untouched disk. Without it, dry-run
    reports every file it is about to migrate as unclassifiable.
    """
    leftovers: list[str] = []
    for name in (LEGACY_INBOX, LEGACY_BOARDS, LEGACY_BOOKS):
        folder = vault_path / name
        if not folder.is_dir():
            continue
        # Prune empty book subfolders first so an emptied Inbox can go too.
        for child in sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True):
            if not any(child.iterdir()) and not dry_run:
                child.rmdir()
        remaining = [
            p for p in folder.rglob("*") if p.is_file() and p not in moved
        ]
        if remaining:
            leftovers += [str(p.relative_to(vault_path)) for p in remaining]
            continue
        if not dry_run:
            shutil.rmtree(folder)
    return leftovers


def apply_migration(vault_path: Path, *, dry_run: bool = True) -> list[Move]:
    """Run every step in order. Returns the moves made, or that would be made."""
    vault_path = Path(vault_path)
    moves: list[Move] = []

    for stem in _legacy_books(vault_path):
        # Sources move first: resolving a note's chapter needs the epub, and
        # epub_source looks in the new location before the old one.
        moves += _migrate_sources(vault_path, stem, dry_run=dry_run)
        note_moves, renames = _migrate_notes(vault_path, stem, dry_run=dry_run)
        moves += note_moves
        moves += _migrate_board(vault_path, stem, renames, dry_run=dry_run)

    moves += _orphan_epubs(vault_path, {m.src for m in moves}, dry_run=dry_run)

    leftovers = _drop_empty_legacy_dirs(
        vault_path, {m.src for m in moves}, dry_run=dry_run
    )
    for left in leftovers:
        log.warning("não soube classificar, mantido no lugar: %s", left)

    if moves and not dry_run:
        bases.write_bases(vault_path)
    return moves


def plan_migration(vault_path: Path) -> list[Move]:
    return apply_migration(vault_path, dry_run=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="mover de verdade (o padrão é dry-run)"
    )
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(message)s")

    moves = apply_migration(args.vault, dry_run=not args.apply)
    if not moves:
        print("nada a fazer: o vault já está no layout novo")
        return 0
    for move in moves:
        print(f"  {move}")
    print(f"\n{len(moves)} movimentos" + ("" if args.apply else " (dry-run)"))
    if not args.apply:
        print("rode de novo com --apply para executar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
