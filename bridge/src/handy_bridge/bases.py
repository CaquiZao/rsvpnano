"""Write the Bases views that make each kind of note one click away.

The vault is cut along three axes and each gets the mechanism that serves it:
directories carry reading position, the Kanban board carries status, and these
views carry kind. A view reads the notes' frontmatter, so unlike a generated
index it cannot fall out of sync — which is why the kind axis costs no code in
the note pipeline at all.

Bases is a core Obsidian feature, so this needs no community plugin. The exact
filter syntax is the one thing here that cannot be verified without opening
Obsidian; the failure mode is chosen deliberately, though. A wrong filter key
makes a view show more rows than intended, never break, and `kind` in the
frontmatter stays findable through native search (`kind: recall`), which can be
saved to Bookmarks. No reading path depends on the filter being right.
"""

from __future__ import annotations

from pathlib import Path

from handy_bridge.layout import BOOKS_DIR

# (filename, kind value, view label)
VIEWS: tuple[tuple[str, str, str], ...] = (
    ("Anotações.base", "anotação", "Anotações"),
    ("Perguntas.base", "pergunta", "Perguntas"),
    ("Recall.base", "recall", "Recall"),
)

_TEMPLATE = """filters:
  and:
    - 'kind == "{kind}"'
    - 'file.inFolder("{books}")'
views:
  - type: table
    name: {label}
    order:
      - book
      - chapter_title
      - title
      - date
      - tags
    sort:
      - property: book
        direction: ASC
      - property: chapter
        direction: ASC
      - property: word_offset
        direction: ASC
"""


def render(kind: str, label: str) -> str:
    return _TEMPLATE.format(kind=kind, label=label, books=BOOKS_DIR)


def write_bases(vault_path: Path) -> list[Path]:
    """Create the three entry points. Rewriting them is safe: they are derived."""
    vault_path = Path(vault_path)
    vault_path.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, kind, label in VIEWS:
        target = vault_path / filename
        tmp = target.with_name(target.name + ".partial")
        tmp.write_text(render(kind, label), encoding="utf-8")
        tmp.replace(target)
        written.append(target)
    return written
