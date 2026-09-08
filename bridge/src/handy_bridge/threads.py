"""Remember which note each Telegram message belongs to.

Persisted to a small JSON file so a follow-up still finds its note after the
bridge restarts. Every message in a thread maps to the same thread id, so the
user can reply to any message in it and still get the full history.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_MAX_HISTORY = 8


@dataclass(frozen=True)
class Thread:
    note_path: Path
    history: list[tuple[str, str]]


class ThreadStore:
    def __init__(self, path: Path, max_history: int = DEFAULT_MAX_HISTORY):
        self._path = Path(path)
        self._max_history = max_history
        self._threads: dict[str, dict] = {}
        self._by_message: dict[str, str] = {}
        self._latest: str | None = None
        self._load()

    # --- leitura ---------------------------------------------------------

    def resolve(self, message_id: int) -> Thread | None:
        thread_id = self._by_message.get(str(message_id))
        return self._thread(thread_id)

    def latest(self) -> Thread | None:
        return self._thread(self._latest)

    def latest_anchor(self) -> int | None:
        """Any message id belonging to the most recent thread.

        A bare Telegram message carries no reply_to, so appending its answer to
        the right thread needs some message id already mapped to that thread.
        """
        if self._latest is None:
            return None
        for message_id, thread_id in self._by_message.items():
            if thread_id == self._latest:
                return int(message_id)
        return None

    def _thread(self, thread_id: str | None) -> Thread | None:
        if thread_id is None:
            return None
        raw = self._threads.get(thread_id)
        if raw is None:
            return None
        return Thread(
            note_path=Path(raw["note_path"]),
            history=[(item[0], item[1]) for item in raw.get("history", [])],
        )

    # --- escrita ---------------------------------------------------------

    def remember(self, message_id: int, note_path: Path, question: str, answer: str) -> None:
        thread_id = str(message_id)
        self._threads[thread_id] = {
            "note_path": str(note_path),
            "history": [[question, answer]],
        }
        self._by_message[str(message_id)] = thread_id
        self._latest = thread_id
        self._save()

    def append(self, message_id: int, question: str, answer: str, new_message_id: int) -> None:
        thread_id = self._by_message.get(str(message_id))
        if thread_id is None or thread_id not in self._threads:
            log.warning("follow-up for an unknown thread (message %s); ignored", message_id)
            return
        history = self._threads[thread_id].setdefault("history", [])
        history.append([question, answer])
        # Cap the history so the follow-up prompt cannot grow without bound.
        del history[: max(0, len(history) - self._max_history)]
        if new_message_id:
            self._by_message[str(new_message_id)] = thread_id
        self._latest = thread_id
        self._save()

    # --- persistencia ----------------------------------------------------

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # A damaged state file must not stop the bridge; it only costs history.
            log.warning("could not read %s (%s); starting with empty threads", self._path, exc)
            return
        self._threads = raw.get("threads", {})
        self._by_message = raw.get("by_message", {})
        self._latest = raw.get("latest")

    def _save(self) -> None:
        payload = {
            "threads": self._threads,
            "by_message": self._by_message,
            "latest": self._latest,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".partial")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
