"""Repair an EPUB whose table of contents points at anchors that do not exist.

Calibre conversions that split a single source file routinely emit a `toc.ncx`
full of entries like `index_split_000.html#22` while none of the documents carry
an `id="22"`. Readers that honour the table of contents — including this one —
then see a book with no chapters at all: 195k words, two chapter markers.

The chapter titles are still in the text, so this rebuilds the link. For each
entry, it finds the title in the reading order, ignoring occurrences that sit
inside an `<a>` element because those are the broken table of contents itself,
inserts the anchor the entry expects, and repoints the entry at the document
that actually holds it.

Usage:
    python tools/repair_epub_toc.py <input.epub> [output.epub]

Entries whose title never appears as running text are left untouched: an anchor
guessed at the wrong place is worse than a missing one.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

NAVPOINT_RE = re.compile(r"<navPoint\b.*?</navPoint>", re.S | re.I)
TEXT_RE = re.compile(r"<text>(.*?)</text>", re.S | re.I)
SRC_RE = re.compile(r'src\s*=\s*"([^"]*)"', re.I)
DOC_SUFFIXES = (".html", ".xhtml", ".htm")


@dataclass
class Entry:
    title: str
    fragment: str
    src: str
    raw: str


def parse_navpoints(ncx: str) -> list[Entry]:
    entries: list[Entry] = []
    for raw in NAVPOINT_RE.findall(ncx):
        text = TEXT_RE.search(raw)
        src = SRC_RE.search(raw)
        if not text or not src:
            continue
        href = src.group(1)
        fragment = href.split("#", 1)[1] if "#" in href else ""
        entries.append(
            Entry(
                title=re.sub(r"<[^>]+>", "", text.group(1)).strip(),
                fragment=fragment,
                src=href.split("#", 1)[0],
                raw=raw,
            )
        )
    return entries


def spine_order(zf: zipfile.ZipFile) -> list[str]:
    """Documents in reading order, so titles are matched as the book flows."""
    opf_name = next((n for n in zf.namelist() if n.lower().endswith(".opf")), None)
    docs = [n for n in zf.namelist() if n.lower().endswith(DOC_SUFFIXES)]
    if not opf_name:
        return sorted(docs)

    opf = zf.read(opf_name).decode("utf-8", "replace")
    base = opf_name.rsplit("/", 1)[0] + "/" if "/" in opf_name else ""
    ids = dict(
        re.findall(r'<item\b[^>]*id\s*=\s*"([^"]+)"[^>]*href\s*=\s*"([^"]+)"', opf, re.I)
    )
    ordered: list[str] = []
    for idref in re.findall(r'<itemref\b[^>]*idref\s*=\s*"([^"]+)"', opf, re.I):
        href = ids.get(idref)
        if not href:
            continue
        name = (base + href).replace("//", "/")
        if name in docs and name not in ordered:
            ordered.append(name)
    ordered += [n for n in sorted(docs) if n not in ordered]
    return ordered


def _inside_link(doc: str, start: int, end: int) -> bool:
    """True when the match sits inside an <a>...</a>, i.e. it is a TOC link."""
    open_at = doc.rfind("<a ", 0, start)
    if open_at == -1:
        return False
    close_at = doc.find("</a>", open_at)
    return close_at != -1 and close_at >= end


def _is_whole_text_node(doc: str, start: int, end: int, title: str) -> bool:
    """True when the title is the entire text of its element, i.e. a heading.

    This is what separates the line that opens a chapter from the same words
    mentioned inside a paragraph. Without it, one early false match pushes the
    scan past the real heading and every later chapter is missed with it.
    """
    open_at = doc.rfind(">", 0, start)
    close_at = doc.find("<", end)
    if open_at == -1 or close_at == -1:
        return False
    node = doc[open_at + 1 : close_at]
    return re.sub(r"\s+", " ", node).strip().lower() == title.strip().lower()


def find_title(doc: str, title: str, from_pos: int) -> int:
    """Offset of the title as running text at or after from_pos, or -1."""
    return _find(doc, title, from_pos, headings_only=False)


def _find(doc: str, title: str, from_pos: int, headings_only: bool) -> int:
    for match in re.finditer(re.escape(title), doc[from_pos:], re.I):
        start = from_pos + match.start()
        end = from_pos + match.end()
        if _inside_link(doc, start, end):
            # The broken table of contents itself; never anchor onto it.
            continue
        if headings_only and not _is_whole_text_node(doc, start, end, title):
            continue
        return start
    return -1


def candidates_for(title: str) -> list[str]:
    """The full title first, then the part after the number, e.g. "3 - FOO"."""
    out = [title]
    if " - " in title:
        tail = title.split(" - ", 1)[1].strip()
        if len(tail) >= 4:
            out.append(tail)
    return out


def _element_start(doc: str, offset: int) -> int:
    """Start of the tag that opens the text, so the anchor lands before it."""
    open_at = doc.rfind("<", 0, offset)
    return open_at if open_at != -1 else offset


def repair(zf: zipfile.ZipFile, ncx_name: str) -> tuple[dict[str, str], str, int, int]:
    """Returns rewritten documents, rewritten ncx, and (repaired, skipped)."""
    ncx = zf.read(ncx_name).decode("utf-8", "replace")
    entries = parse_navpoints(ncx)
    order = spine_order(zf)
    docs = {name: zf.read(name).decode("utf-8", "replace") for name in order}

    # Insertions are collected per document and applied at the end, so offsets
    # found during the scan stay valid while scanning.
    pending: dict[str, list[tuple[int, str]]] = {name: [] for name in order}
    doc_index = 0
    position = 0
    repaired = 0
    skipped = 0
    # Two entries must never claim the same spot, or the second silently
    # overwrites the first and one chapter disappears.
    taken: set[tuple[int, int]] = set()

    for entry in entries:
        if not entry.fragment:
            skipped += 1
            continue

        # Four passes, most trustworthy first. A heading ahead of the scan is the
        # ideal; a heading behind it still beats a mention inside a paragraph,
        # because one bad match early would otherwise cost every chapter after it.
        located: tuple[int, int] | None = None
        for headings_only in (True, False):
            for from_start in (False, True):
                for candidate in candidates_for(entry.title):
                    search_doc = 0 if from_start else doc_index
                    search_pos = 0 if from_start else position
                    while search_doc < len(order):
                        hit = _find(docs[order[search_doc]], candidate, search_pos, headings_only)
                        if hit != -1 and (search_doc, hit) not in taken:
                            located = (search_doc, hit)
                            break
                        search_doc += 1
                        search_pos = 0
                    if located:
                        break
                if located:
                    break
            if located:
                break

        if not located:
            # No running text for this title. Leaving the entry alone keeps the
            # book honest: a missing chapter beats one marked in the wrong place.
            skipped += 1
            continue

        found_doc, offset = located
        taken.add(located)
        name = order[found_doc]
        anchor_at = _element_start(docs[name], offset)
        pending[name].append((anchor_at, f'<span id="{entry.fragment}"></span>'))
        ncx = ncx.replace(entry.raw, entry.raw.replace(f'"{entry.src}#', f'"{name}#'), 1)
        if (found_doc, offset) >= (doc_index, position):
            doc_index, position = found_doc, offset + 1
        repaired += 1

    rewritten: dict[str, str] = {}
    for name, inserts in pending.items():
        if not inserts:
            continue
        doc = docs[name]
        for at, markup in sorted(inserts, reverse=True):
            doc = doc[:at] + markup + doc[at:]
        rewritten[name] = doc
    return rewritten, ncx, repaired, skipped


def write_epub(source: Path, target: Path, changed: dict[str, str], ncx_name: str, ncx: str) -> None:
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(
        target, "w", zipfile.ZIP_DEFLATED
    ) as out:
        for item in src.infolist():
            if item.filename == ncx_name:
                out.writestr(item, ncx.encode("utf-8"))
            elif item.filename in changed:
                out.writestr(item, changed[item.filename].encode("utf-8"))
            else:
                out.writestr(item, src.read(item.filename))


def verify(path: Path) -> tuple[int, int]:
    """Count entries whose target document really contains the anchor now."""
    with zipfile.ZipFile(path) as zf:
        ncx_name = next(n for n in zf.namelist() if n.lower().endswith(".ncx"))
        entries = parse_navpoints(zf.read(ncx_name).decode("utf-8", "replace"))
        good = bad = 0
        for entry in entries:
            if not entry.fragment:
                continue
            try:
                doc = zf.read(entry.src).decode("utf-8", "replace")
            except KeyError:
                bad += 1
                continue
            if f'id="{entry.fragment}"' in doc:
                good += 1
            else:
                bad += 1
    return good, bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path, nargs="?")
    args = parser.parse_args(argv)

    target = args.target or args.source.with_name(args.source.stem + " (capitulos).epub")

    with zipfile.ZipFile(args.source) as zf:
        ncx_name = next((n for n in zf.namelist() if n.lower().endswith(".ncx")), None)
        if not ncx_name:
            print("nenhum toc.ncx neste EPUB; nada a reparar", file=sys.stderr)
            return 1
        before_good, before_bad = 0, 0
        for entry in parse_navpoints(zf.read(ncx_name).decode("utf-8", "replace")):
            if not entry.fragment:
                continue
            try:
                doc = zf.read(entry.src).decode("utf-8", "replace")
            except KeyError:
                before_bad += 1
                continue
            if f'id="{entry.fragment}"' in doc:
                before_good += 1
            else:
                before_bad += 1
        changed, ncx, repaired, skipped = repair(zf, ncx_name)

    write_epub(args.source, target, changed, ncx_name, ncx)
    after_good, after_bad = verify(target)

    print(f"antes:  {before_good} ancoras validas, {before_bad} quebradas")
    print(f"depois: {after_good} ancoras validas, {after_bad} quebradas")
    print(f"inseridas: {repaired}   sem texto correspondente: {skipped}")
    print(f"gravado em: {target}")
    return 0 if after_good > before_good else 1


if __name__ == "__main__":
    raise SystemExit(main())
