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
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

log = logging.getLogger(__name__)

CONTAINER_PATH = "META-INF/container.xml"
_SKIP_TAGS = {"style", "script", "head", "title"}
_HEADINGS = {
    "h1": "#",
    "h2": "##",
    "h3": "###",
    "h4": "####",
    "h5": "#####",
    "h6": "######",
}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "blockquote", *_HEADINGS}


class EpubError(Exception):
    """Raised when a file is not a usable epub."""


class _ChapterParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _HEADINGS:
            self._parts.append("\n\n")
            self._parts.append(_HEADINGS[tag] + " ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _HEADINGS:
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
            hrefs.append(
                posixpath.normpath(posixpath.join(base, href)) if base else href
            )
    return hrefs


@dataclass(frozen=True)
class RawChapter:
    """One spine entry, with its title peeled off the body.

    `heading` keeps the original markdown heading line so `convert_to_markdown`
    can reproduce the level the book actually used. Without it, rebuilding the
    flat markdown from chapters would normalise every title to `#` and change
    output that is already being consumed elsewhere.
    """

    title: str
    text: str
    heading: str | None = None


_HEADING_LINE = re.compile(r"^#{1,6}[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)

# A heading further in than this is a section break inside the chapter, not the
# chapter's own title.
_TITLE_SEARCH_LIMIT = 200


def _split_title(text: str, fallback: str) -> tuple[str, str, str | None]:
    """Return (title, body without the heading, original heading line)."""
    match = _HEADING_LINE.search(text)
    if match is None or match.start() > _TITLE_SEARCH_LIMIT:
        return fallback, text, None
    title = match.group("title").strip()
    body = (text[: match.start()] + text[match.end() :]).strip()
    return title or fallback, body, match.group(0).strip()


def convert_to_chapters(epub_path: Path) -> list[RawChapter]:
    """Split an epub into chapters, one per spine entry."""
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
            title, body, heading = _split_title(text, f"Capítulo {len(chapters) + 1}")
            chapters.append(RawChapter(title=title, text=body, heading=heading))
    return chapters


def convert_to_markdown(epub_path: Path, out_path: Path) -> Path:
    out_path = Path(out_path)
    chunks = [
        f"{chapter.heading}\n\n{chapter.text}" if chapter.heading else chapter.text
        for chapter in convert_to_chapters(epub_path)
    ]

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
