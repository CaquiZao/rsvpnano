"""Work out which chapter a voice note was recorded in.

The device counts words with its own tokenizer, built from the epub on the board;
the bridge parses the same epub in Python. The two counts do not match, and never
will — different HTML stripping, different whitespace rules, different handling of
punctuation. So the chapter is resolved by finding the device's excerpt inside the
converted text, and `word_offset` is used only to break ties, which is the one job
an approximate offset does reliably.

Text is normalised *per chapter* rather than across the whole book on purpose: a
match inside chapter N returns N directly, with no need to map a position in the
normalised string back to the original. That removes the most likely class of bug
here entirely.
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

# Below this many characters an excerpt matches too much text to identify a
# position. Real excerpts arrive as whole paragraphs, so this only rejects the
# degenerate cases.
MIN_EXCERPT_CHARS = 24


def normalize(text: str) -> str:
    """Reduce text to what survives both conversion paths.

    Accents are folded and punctuation collapses to spaces, because the device's
    excerpt and the bridge's markdown disagree on both.
    """
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
    """Map an offset onto a chapter by walking the bridge's own word counts."""
    if not index.chapters or index.word_count <= 0:
        return None
    running = 0
    for chapter in index.chapters:
        running += chapter.word_count
        if word_offset < running:
            return Resolution(chapter.index, chapter.title, "estimado")
    last = index.chapters[-1]
    return Resolution(last.index, last.title, "estimado")


def compact(normalized: str) -> str:
    """Drop spaces so a word-join artefact cannot break a match.

    The device runs its own HTML-to-text conversion, and it sometimes welds two
    words together: a real excerpt from the vault reads "passaram porum processo"
    where the book says "por um". Comparing without spaces makes that class of
    artefact invisible. Word boundaries are lost, but an excerpt of at least
    MIN_EXCERPT_CHARS characters does not collide by accident across a book.
    """
    return normalized.replace(" ", "")


def _match(index: BookIndex, needle: str) -> list[Chapter]:
    """Chapters containing the excerpt, tried strictly before loosely."""
    hits = [c for c in index.chapters if needle in c.normalized]
    if hits:
        return hits
    loose = compact(needle)
    return [c for c in index.chapters if loose in compact(c.normalized)]


def resolve(
    index: BookIndex, excerpt: str | None, word_offset: int | None
) -> Resolution | None:
    """Find the chapter, preferring the excerpt and falling back to the offset."""
    needle = normalize(excerpt or "")
    if len(needle) >= MIN_EXCERPT_CHARS:
        hits = _match(index, needle)
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


def index_cache_path(source_dir: Path, book_stem: str) -> Path:
    return Path(source_dir) / f"{book_stem}.chapters.json"


def load_index(source_dir: Path, book_stem: str, epub_path: Path) -> BookIndex | None:
    """Build the index once per book and cache it beside the converted text.

    Returns None rather than raising: an unreadable book is a missing convenience,
    never a reason to lose a note.
    """
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

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        payload = {"chapters": [c.__dict__ for c in index.chapters]}
        tmp = cache.with_name(cache.name + ".partial")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache)
    except OSError as exc:
        # Caching is an optimisation; a read-only vault must not break resolution.
        log.warning("could not cache the chapter index for %r: %s", book_stem, exc)
    return index
