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
from datetime import datetime
from pathlib import Path

from handy_bridge import chapters as chapters_mod, layout
from handy_bridge.epub import EpubError, convert_to_markdown, epub_source
from handy_bridge.kind import DEFAULT_KIND, VALID_KINDS, spoken_recall_marker
from handy_bridge.note import slugify

log = logging.getLogger(__name__)

LEGACY_INBOX = "Inbox"
LEGACY_BOARDS = "Quadros"
LEGACY_BOOKS = "Books"
# The single notes directory this vault used before notes were split by kind.
LEGACY_NOTES = "Notas"
# Bases views were replaced by one directory per kind, so their files go too.
LEGACY_BASES = ("Anotações.base", "Perguntas.base", "Recall.base")

QUOTE_CALLOUT = "> [!quote]"
QUESTION_CALLOUT = "> [!question]"
RAW_CALLOUT = "> [!note]-"
SETTINGS_MARKER = "%% kanban:settings"

_OFFSET = re.compile(r"^word_offset:\s*(\d+)\s*$", re.MULTILINE)
_TITLE = re.compile(r'^title:\s*"?(?P<title>.*?)"?\s*$', re.MULTILINE)
_KIND = re.compile(r"^kind:\s*(?P<kind>\S+)\s*$", re.MULTILINE)
_DATE = re.compile(r"^date:\s*(?P<date>\S+)\s*$", re.MULTILINE)
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_CARD = re.compile(r"^- \[(?P<mark>[ xX])\]\s*(?P<body>.*)$")


@dataclass(frozen=True)
class Move:
    src: Path
    dst: Path
    why: str

    def __str__(self) -> str:
        # A removal has nowhere to point at, so showing an arrow would be a lie.
        if self.src == self.dst:
            return f"{self.why}: {self.src.name}"
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


def read_kind(text: str) -> str | None:
    """A kind already written by an earlier pass. Trusted over reclassifying."""
    match = _KIND.search(text)
    if match is None:
        return None
    value = match.group("kind").strip()
    return value if value in VALID_KINDS else None


def read_date(text: str) -> datetime | None:
    """The recorded moment, which is what the filename is built from now."""
    match = _DATE.search(text)
    if match is None:
        return None
    try:
        return datetime.fromisoformat(match.group("date").strip())
    except ValueError:
        return None


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
    """Book stems that still have something outside the current layout."""
    inbox = vault_path / LEGACY_INBOX
    stems = {p.name for p in inbox.iterdir() if p.is_dir()} if inbox.is_dir() else set()
    boards = vault_path / LEGACY_BOARDS
    if boards.is_dir():
        stems |= {p.stem for p in boards.glob("*.md")}
    # A vault reorganised before notes were split by kind has a single `Notas`
    # directory per book; those notes still need routing.
    books = vault_path / layout.BOOKS_DIR
    if books.is_dir():
        stems |= {
            p.name for p in books.iterdir() if (p / LEGACY_NOTES).is_dir()
        }
    if (vault_path / layout.GENERAL_DIR / LEGACY_NOTES).is_dir():
        stems.add("")
    return sorted(stems)


def _note_sources(vault_path: Path, stem: str) -> list[Path]:
    """Directories a book's notes may still be sitting in."""
    candidates = [
        vault_path / LEGACY_INBOX / stem if stem else None,
        layout.book_dir(vault_path, stem) / LEGACY_NOTES,
    ]
    return [path for path in candidates if path is not None and path.is_dir()]


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
    sources = _note_sources(vault_path, stem)
    if not sources:
        return [], {}

    index = chapters_mod.load_index(
        layout.source_dir(vault_path, stem),
        stem,
        epub_source(vault_path, stem),
        # A dry run must not leave a cache file behind.
        write_cache=not dry_run,
    )

    moves: list[Move] = []
    renames: dict[str, str] = {}
    taken: set[Path] = set()
    notes = sorted(
        (note for source in sources for note in source.glob("*.md")),
        key=lambda p: p.name,
    )
    for note in notes:
        text = note.read_text(encoding="utf-8")
        excerpt, offset = read_excerpt(text), read_offset(text)
        resolved = chapters_mod.resolve(index, excerpt, offset) if index else None
        kind = read_kind(text) or classify(text)

        additions = [f"kind: {kind}"]
        if resolved is not None:
            additions += [
                f"chapter: {resolved.chapter}",
                f'chapter_title: "{resolved.title}"',
                f"chapter_source: {resolved.source}",
            ]

        title = read_title(text, note.stem)
        recorded = read_date(text)
        # Falling back to the existing stem keeps a note whose frontmatter has no
        # usable date from being renamed to something worse than what it had.
        new_stem = layout.note_stem(recorded, title) if recorded else note.stem
        # One directory per kind: that is the whole point of this pass.
        target = _unique(layout.notes_dir(vault_path, stem, kind), new_stem, taken)
        taken.add(target)
        renames[note.stem] = target.stem
        moves.append(Move(note, target, f"nota → {kind}"))

        if not dry_run:
            _write_atomic(target, add_frontmatter(text, additions))
            note.unlink()

    if moves and not dry_run:
        # Even the kinds this book has none of, so the layout explains itself.
        layout.ensure_kind_dirs(vault_path, stem)
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


def _rename_to_dates(
    vault_path: Path, *, dry_run: bool
) -> tuple[list[Move], dict[str, dict[str, str]]]:
    """Rename notes already in a kind directory but still carrying a coded name.

    The position-coded filename was an intermediate step. A note that reached its
    kind directory under that convention would otherwise keep the code forever,
    since the routing pass only looks at the directories notes come *from*.

    Idempotent because the target stem is computed from the note's own date: once
    renamed, the computed stem equals the current one and nothing moves.
    """
    moves: list[Move] = []
    renames: dict[str, dict[str, str]] = {}
    for stem in _current_books(vault_path):
        for folder in layout.all_notes_dirs(vault_path, stem):
            if not folder.is_dir():
                continue
            for note in sorted(folder.glob("*.md")):
                text = note.read_text(encoding="utf-8")
                recorded = read_date(text)
                if recorded is None:
                    continue
                wanted = layout.note_stem(recorded, read_title(text, note.stem))
                if wanted == note.stem:
                    continue
                target = _unique(folder, wanted, set())
                moves.append(Move(note, target, "nota renomeada"))
                renames.setdefault(stem, {})[note.stem] = target.stem
                if not dry_run:
                    note.replace(target)
    return moves, renames


def _current_books(vault_path: Path) -> list[str | None]:
    """Books that already have a directory in the current layout."""
    out: list[str | None] = []
    books = vault_path / layout.BOOKS_DIR
    if books.is_dir():
        out += [p.name for p in sorted(books.iterdir()) if p.is_dir()]
    if (vault_path / layout.GENERAL_DIR).is_dir():
        out.append(None)
    return out


def _relink_board(
    vault_path: Path, stem: str | None, renames: dict[str, str], *, dry_run: bool
) -> list[Move]:
    """Point an existing board's cards at notes that were just renamed."""
    board = layout.board_path(vault_path, stem)
    if not board.is_file() or not renames:
        return []
    if not dry_run:
        _write_atomic(board, rewrite_board(board.read_text(encoding="utf-8"), renames))
    return [Move(board, board, "quadro religado")]


def _fill_kind_dirs(vault_path: Path, *, dry_run: bool) -> list[Move]:
    """Create any kind directory a book is missing.

    Runs even when there is nothing to move, because a vault reorganised before
    the kinds became directories has only the ones it happened to need. Reports
    only the directories it actually had to create, which is what keeps a second
    run reporting nothing.
    """
    moves: list[Move] = []
    for stem in _current_books(vault_path):
        for folder in layout.all_notes_dirs(vault_path, stem):
            if folder.is_dir():
                continue
            moves.append(Move(folder, folder, "pasta criada"))
            if not dry_run:
                folder.mkdir(parents=True, exist_ok=True)
    return moves


def _drop_bases(vault_path: Path, *, dry_run: bool) -> list[Move]:
    """Remove the Bases views, replaced by one directory per kind.

    Safe to delete outright: they held no content of their own, only a query over
    the notes' frontmatter.
    """
    moves: list[Move] = []
    for name in LEGACY_BASES:
        path = vault_path / name
        if not path.is_file():
            continue
        moves.append(Move(path, path, "base removida"))
        if not dry_run:
            path.unlink()
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
    folders = [vault_path / name for name in (LEGACY_INBOX, LEGACY_BOARDS, LEGACY_BOOKS)]
    # The per-book `Notas` directories go the same way once their notes are routed.
    books = vault_path / layout.BOOKS_DIR
    if books.is_dir():
        folders += [p / LEGACY_NOTES for p in sorted(books.iterdir()) if p.is_dir()]
    folders.append(vault_path / layout.GENERAL_DIR / LEGACY_NOTES)

    for folder in folders:
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

    # Notes that reached their kind directory under the old coded name.
    renamed, renames = _rename_to_dates(vault_path, dry_run=dry_run)
    moves += renamed
    for stem, mapping in renames.items():
        moves += _relink_board(vault_path, stem, mapping, dry_run=dry_run)

    moves += _drop_bases(vault_path, dry_run=dry_run)
    moves += _fill_kind_dirs(vault_path, dry_run=dry_run)

    leftovers = _drop_empty_legacy_dirs(
        vault_path, {m.src for m in moves}, dry_run=dry_run
    )
    for left in leftovers:
        log.warning("não soube classificar, mantido no lugar: %s", left)

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
