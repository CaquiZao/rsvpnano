"""Write cards into an obsidian-kanban board.

The file format is taken from the plugin's own source (obsidian-kanban 2.0.51):
a new board is `["---", "", "kanban-plugin: board", "", "---", "", ""]`, lanes are
`## Heading`, cards are `- [ ] text`, and a `%% kanban:settings` paragraph sits at
the bottom. Insertion is line-based on purpose — a full Markdown round-trip would
risk reformatting a board the user edits by hand.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

FRONTMATTER = "---\n\nkanban-plugin: board\n\n---\n"
SETTINGS_MARKER = "%% kanban:settings"
SETTINGS_BLOCK = f'{SETTINGS_MARKER}\n```\n{{"kanban-plugin":"board"}}\n```\n%%\n'

TRIAGE_LANE = "Triagem"
TODO_LANE = "A pesquisar"
DOING_LANE = "Pesquisando"
DONE_LANE = "Concluído"
LANES = (TRIAGE_LANE, TODO_LANE, DOING_LANE, DONE_LANE)

GENERAL_BOARD = "Geral"


class KanbanError(Exception):
    """Raised when a target file is not a usable Kanban board."""


def board_path_for(vault_path: Path, subfolder: str, book_stem: str | None) -> Path:
    """One board per book; recordings made outside the reader share a general board.

    `subfolder` is accepted and ignored: the board now lives inside the book's own
    directory, next to its notes and summaries. The parameter stays so an existing
    config.toml with `[kanban] subfolder` keeps loading.
    """
    from handy_bridge.layout import board_path

    del subfolder
    return board_path(vault_path, book_stem)


def _strip_marker(text: str) -> str:
    """Remove a list or checkbox marker the model may have already added."""
    body = text.strip()
    for prefix in ("- [ ] ", "- [] ", "- ", "* "):
        if body.startswith(prefix):
            return body[len(prefix) :].strip()
    return body


def render_card(text: str, note_name: str | None, done: bool = False) -> str:
    body = _strip_marker(text)
    # A card sitting in the "done" lane must be checked; unchecked there contradicts
    # the lane it is in, and the plugin treats the checkbox as the real state.
    box = "- [x] " if done else "- [ ] "
    if note_name:
        return f"{box}{body} [[{note_name}]]"
    return f"{box}{body}"


def as_card_line(entry: str) -> str:
    """Accept either raw task text or an already-rendered card line.

    add_cards() normalizes every entry through this so a caller that forgets
    render_card() cannot write a malformed line into the user's board.
    """
    if entry.startswith("- [ ] ") or entry.startswith("- [x] "):
        return entry.rstrip()
    return f"- [ ] {_strip_marker(entry)}"


def ensure_board(path: Path) -> Path:
    """Create the board with all lanes if it does not exist yet. Never overwrites."""
    path = Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = [FRONTMATTER, "\n"]
    for lane in LANES:
        parts.append(f"## {lane}\n\n")
    parts.append(SETTINGS_BLOCK)
    _write_atomic(path, "".join(parts))
    log.info("created Kanban board %s", path)
    return path


def add_cards(path: Path, lane: str, cards: list[str]) -> int:
    """Insert cards at the end of `lane`. Returns how many were written."""
    path = Path(path)
    if not cards:
        return 0

    text = path.read_text(encoding="utf-8")
    if "kanban-plugin" not in text:
        raise KanbanError(f"not a Kanban board: {path}")

    lines = text.splitlines()
    heading = f"## {lane}"

    if heading in lines:
        at = lines.index(heading)
        insert_at = _lane_end(lines, at)
    else:
        # Unknown lane: add it just above the settings block so the plugin still parses.
        insert_at = _settings_start(lines)
        lines[insert_at:insert_at] = [heading, ""]
        insert_at += 1

    lines[insert_at:insert_at] = [as_card_line(card) for card in cards]
    _write_atomic(path, "\n".join(lines) + "\n")
    log.info("added %d card(s) to %s in %s", len(cards), lane, path.name)
    return len(cards)


def _lane_end(lines: list[str], heading_at: int) -> int:
    """Index just past the last card of the lane that starts at `heading_at`."""
    end = heading_at + 1
    last_content = heading_at
    while end < len(lines):
        line = lines[end]
        if line.startswith("## ") or line.startswith(SETTINGS_MARKER):
            break
        if line.strip():
            last_content = end
        end += 1
    return last_content + 1


def _settings_start(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if line.startswith(SETTINGS_MARKER):
            return index
    return len(lines)


def _write_atomic(path: Path, content: str) -> None:
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
