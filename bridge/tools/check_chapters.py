"""Report how the chapter resolver does against the notes already in the vault.

This is the gate the design document puts between organising the vault and
building summaries on top of it. Chapter resolution is the foundation for the
note filename, the chapter summary and the recall passage window, and it is the
only part whose correctness is a measurement rather than a consequence of the
design. Run it before building anything that depends on it:

    uv run python tools/check_chapters.py "<vault>" "<book stem>"

Exit code 0 means every note resolved exactly.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from handy_bridge import chapters as chapters_mod  # noqa: E402

QUOTE = "> [!quote]"
OFFSET = re.compile(r"^word_offset:\s*(\d+)\s*$", re.MULTILINE)


def read_excerpt(text: str) -> str | None:
    """Pull the book text back out of the note's quote callout."""
    lines = text.splitlines()
    try:
        at = next(i for i, line in enumerate(lines) if line.startswith(QUOTE))
    except StopIteration:
        return None
    out = []
    for line in lines[at + 1 :]:
        if not line.startswith(">"):
            break
        out.append(line.lstrip(">").strip())
    return " ".join(out).strip() or None


def find_epub(vault: Path, stem: str) -> Path:
    """Look in the new layout first, then where the vault keeps it today."""
    candidates = [
        vault / "Livros" / stem / "fonte" / f"{stem}.epub",
        vault / f"{stem}.epub",
        vault / "Books" / f"{stem}.epub",
    ]
    return next((c for c in candidates if c.is_file()), candidates[-1])


def find_notes(vault: Path, stem: str) -> list[Path]:
    for folder in (vault / "Inbox" / stem, vault / "Livros" / stem / "Notas"):
        found = sorted(folder.glob("*.md"))
        if found:
            return found
    return []


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    vault, stem = Path(sys.argv[1]), sys.argv[2]

    epub = find_epub(vault, stem)
    if not epub.is_file():
        print(f"FALHA: nenhum epub encontrado para {stem!r} em {vault}")
        return 2

    index = chapters_mod.load_index(vault / "Livros" / stem / "fonte", stem, epub)
    if index is None:
        print(f"FALHA: nao consegui indexar {epub}")
        return 2
    print(f"{epub.name}")
    print(f"{len(index.chapters)} capitulos, {index.word_count} palavras\n")

    notes = find_notes(vault, stem)
    if not notes:
        print(f"FALHA: nenhuma nota encontrada para {stem!r}")
        return 2

    exact = 0
    for note in notes:
        text = note.read_text(encoding="utf-8")
        excerpt = read_excerpt(text)
        match = OFFSET.search(text)
        offset = int(match.group(1)) if match else None
        got = chapters_mod.resolve(index, excerpt, offset)
        if got is None:
            verdict = "SEM RESOLUCAO"
        else:
            verdict = f"cap {got.chapter:>2} [{got.source}] {got.title[:28]}"
            exact += got.source == "exato"
        has = "trecho" if excerpt else "SEM TRECHO"
        print(f"  {note.name[:52]:52} off={offset!s:>7} {has:>10}  {verdict}")

    print(f"\nexatos: {exact}/{len(notes)}")
    return 0 if exact == len(notes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
