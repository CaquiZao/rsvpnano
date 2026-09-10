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

from pathlib import Path, PurePosixPath

from handy_bridge.note import slugify

BOOKS_DIR = "Livros"
GENERAL_DIR = "Geral"
NOTES_DIR = "Notas"
CHAPTERS_DIR = "Capítulos"
SOURCE_DIR = "fonte"
BOARD_FILE = "Quadro.md"

# Wide enough for any book: Sapiens alone counts 147k words.
OFFSET_DIGITS = 6
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


def notes_dir(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / NOTES_DIR


def chapters_dir(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / CHAPTERS_DIR


def source_dir(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / SOURCE_DIR


def board_path(vault_path: Path, book_stem: str | None) -> Path:
    return book_dir(vault_path, book_stem) / BOARD_FILE


def book_summary_path(vault_path: Path, book_stem: str | None) -> Path:
    stem = safe_stem(book_stem) or GENERAL_DIR
    return book_dir(vault_path, book_stem) / f"{slugify(stem)}.md"


def note_stem(
    chapter: int | None, word_offset: int | None, title: str, width: int
) -> str:
    """Name a note by where it sits in the book, not by when it was recorded.

    Reading order is not recording order: re-reading chapter 2 after chapter 8 has
    to put the note back at chapter 2. The date stays in the frontmatter, where the
    Bases views can still sort by it.
    """
    number = str(chapter if chapter is not None else 0).zfill(
        max(MIN_CHAPTER_DIGITS, width)
    )
    offset = str(word_offset if word_offset is not None else 0).zfill(OFFSET_DIGITS)
    return f"{number}-{offset} {slugify(title)}"


def chapter_summary_path(
    vault_path: Path, book_stem: str | None, chapter: int, title: str, width: int
) -> Path:
    number = str(chapter).zfill(max(MIN_CHAPTER_DIGITS, width))
    return chapters_dir(vault_path, book_stem) / f"{number} - {slugify(title)}.md"
