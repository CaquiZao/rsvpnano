"""Weekly nudge listing what is still open on the Kanban boards.

The triage lane accumulates cards the model inferred rather than ones the user
asked for outright, so without a periodic reminder it quietly rots and the board
stops being trusted. This reuses the Telegram channel that already exists.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

from handy_bridge import kanban

log = logging.getLogger(__name__)

# Only the lanes that represent work not yet started.
REPORTED_LANES = (kanban.TRIAGE_LANE, kanban.TODO_LANE)
DEFAULT_MAX_PER_LANE = 8

_WIKILINK = re.compile(r"\s*\[\[[^\]]*\]\]\s*$")


def collect_pending(vault_path: Path, subfolder: str) -> dict[str, dict[str, list[str]]]:
    """Read every board and return the open cards, per board and lane."""
    folder = Path(vault_path) / subfolder
    if not folder.is_dir():
        return {}

    pending: dict[str, dict[str, list[str]]] = {}
    for board in sorted(folder.glob("*.md")):
        try:
            lines = board.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.warning("could not read %s: %s", board, exc)
            continue
        if not any("kanban-plugin" in line for line in lines[:6]):
            continue

        lanes: dict[str, list[str]] = {}
        current: str | None = None
        for line in lines:
            if line.startswith("## "):
                current = line[3:].strip()
                continue
            if line.startswith(kanban.SETTINGS_MARKER):
                break
            # "- [x]" is done; only open cards are a nudge.
            if current in REPORTED_LANES and line.startswith("- [ ] "):
                text = _WIKILINK.sub("", line[6:]).strip()
                if text:
                    lanes.setdefault(current, []).append(text)
        if lanes:
            pending[board.stem] = lanes
    return pending


def render_digest(
    pending: dict[str, dict[str, list[str]]], max_per_lane: int = DEFAULT_MAX_PER_LANE
) -> str:
    if not pending:
        return "📋 Nada em aberto nos seus quadros esta semana."

    total = sum(len(cards) for lanes in pending.values() for cards in lanes.values())
    out = [f"📋 {total} pendência(s) em aberto:"]
    for board, lanes in pending.items():
        out.append("")
        out.append(f"📖 {board}")
        for lane in REPORTED_LANES:
            cards = lanes.get(lane)
            if not cards:
                continue
            out.append(f"  {lane}:")
            for card in cards[:max_per_lane]:
                out.append(f"    • {card}")
            hidden = len(cards) - max_per_lane
            if hidden > 0:
                out.append(f"    … e mais {hidden}")
    return "\n".join(out)


def should_send(
    now: datetime, last_sent: datetime | None, weekday: int, hour: int
) -> bool:
    """True on the configured weekday, at or after the hour, once per day.

    Sending late is deliberate: if the machine was off at the chosen hour, a
    reminder that arrives in the evening still beats one that never arrives.
    """
    if now.weekday() != weekday or now.hour < hour:
        return False
    if last_sent is not None and last_sent.date() == now.date():
        return False
    return True


class DigestState:
    def __init__(self, path: Path):
        self._path = Path(path)

    def last_sent(self) -> datetime | None:
        if not self._path.is_file():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return datetime.fromisoformat(raw["last_sent"])
        except (json.JSONDecodeError, OSError, KeyError, ValueError, TypeError):
            # A damaged file only costs one duplicate digest, so it stays quiet.
            return None

    def record(self, when: datetime) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".partial")
        tmp.write_text(json.dumps({"last_sent": when.isoformat()}), encoding="utf-8")
        os.replace(tmp, self._path)


class DigestScheduler:
    """Wakes periodically and sends the weekly digest when its window arrives."""

    def __init__(self, cfg, telegram, state: DigestState, tick_s: int = 600):
        self._cfg = cfg
        self._telegram = telegram
        self._state = state
        self._tick_s = tick_s
        self._thread = None
        self._stop = None

    def start(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="digest", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.wait(self._tick_s):
            try:
                self.tick(datetime.now())
            except Exception:
                # A digest is a convenience; never let it take the service down.
                log.exception("digest tick failed")

    def tick(self, now: datetime) -> bool:
        """Send if the window is open. Returns whether anything was sent."""
        if not should_send(
            now, self._state.last_sent(), self._cfg.digest.weekday, self._cfg.digest.hour
        ):
            return False
        pending = collect_pending(self._cfg.vault_path, self._cfg.kanban.subfolder)
        self._telegram.send(render_digest(pending))
        self._state.record(now)
        log.info("weekly digest sent (%d board(s))", len(pending))
        return True
