"""Every path inside the vault, in one place.

The layout used to be computed in three modules with three conventions — the note
folder in `note`, the board in `kanban`, the converted text in `epub` — which is
how a single book ended up living in four directories. The new layout derives six
paths per book, and spreading those the same way would repeat the problem, so they
all live here.

Directories are named from the book stem the device sends, not from the book's
title. The stem is the join key between device and vault: renaming it would break
the ability to find a book's existing notes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath

from handy_bridge.kind import DEFAULT_KIND
from handy_bridge.note import slugify

BOOKS_DIR = "Livros"
GENERAL_DIR = "Geral"
CHAPTERS_DIR = "Capítulos"

# One directory per kind, so opening the vault and reading one kind of note is a
# click in the file explorer rather than a query. The cost is that a chapter's
# notes no longer sit together in reading order — the chapter summary is where
# that view lives now.
KIND_DIRS: dict[str, str] = {
    "anotação": "Anotações",
    "pergunta": "Perguntas",
    "recall": "Recall",
}
SOURCE_DIR = "fonte"
BOARD_FILE = "Quadro.md"

MIN_CHAPTER_DIGITS = 2


def safe_stem(book_stem: str | None) -> str:
    """Reduce a device-supplied stem to one safe directory name.

    Returns "" when there is no usable book, which callers read as "no book" —
    deliberately not GENERAL_DIR, so a book actually named "Geral" cannot end up
    sharing a directory with the book-less notes.
    """
    name = (book_stem or "").strip()
    name = PurePosixPath(name.replace("\\", "/")).name if name else ""
    if name in {"", ".", ".."}:
        return ""
    return name


def book_dir(vault_path: Path, book_stem: str | None) -> Path:
    stem = safe_stem(book_stem)
    if not stem:
        return Path(vault_path) / GENERAL_DIR
    return Path(vault_path) / BOOKS_DIR / stem


def notes_dir(vault_path: Path, book_stem: str | None, kind: str = DEFAULT_KIND) -> Path:
    """Where a note of this kind lives. An unknown kind falls back to the default."""
    folder = KIND_DIRS.get(kind) or KIND_DIRS[DEFAULT_KIND]
    return book_dir(vault_path, book_stem) / folder


def all_notes_dirs(vault_path: Path, book_stem: str | None) -> list[Path]:
    """Every kind directory, for callers that need a book's notes as a whole."""
    root = book_dir(vault_path, book_stem)
    return [root / folder for folder in KIND_DIRS.values()]


def ensure_kind_dirs(vault_path: Path, book_stem: str | None) -> list[Path]:
    """Create all three kind directories, even the ones still empty.

    When the kind axis was a query, an absent kind simply returned no rows. As
    directories, an absent one is invisible: opening a book and seeing no `Recall`
    leaves nowhere obvious for a recall to land. Creating them up front makes the
    layout explain itself.
    """
    dirs = all_notes_dirs(vault_path, book_stem)
    for folder in dirs:
        folder.mkdir(parents=True, exist_ok=True)
    return dirs


def chapters_dir(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / CHAPTERS_DIR


def source_dir(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / SOURCE_DIR


def board_path(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / BOARD_FILE


def book_summary_path(vault_path: Path, book_stem: str | None) -> Path:
    stem = safe_stem(book_stem) or GENERAL_DIR
    return book_dir(vault_path, book_stem) / f"{slugify(stem)}.md"


def note_stem(recorded_at: datetime, title: str) -> str:
    """Name a note by when it was recorded, in a form that reads as a date.

    An earlier version encoded chapter and word offset here, so a directory listing
    came out in reading order. That mattered while every note shared one directory;
    once the notes split by kind, the chapter summary became the place where a
    chapter's notes sit together in reading order, and the filename was left
    carrying a code nobody reads. Position lives in the frontmatter, which is where
    the summaries take it from.
    """
    return f"{recorded_at:%Y-%m-%d %H%M} - {slugify(title)}"


def chapter_summary_path(
    vault_path: Path, book_stem: str | None, chapter: int, title: str, width: int
) -> Path:
    number = str(chapter).zfill(max(MIN_CHAPTER_DIGITS, width))
    return chapters_dir(vault_path, book_stem) / f"{number} - {slugify(title)}.md"
