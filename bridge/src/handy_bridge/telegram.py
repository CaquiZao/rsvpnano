"""Push an answered question to Telegram.

Deliberately a plain HTTP POST rather than a Telegram SDK: the Bot API needs one
endpoint and no auth flow, so a dependency would cost more than it saves.
"""

from __future__ import annotations

import logging
from typing import Callable

import httpx

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_MESSAGE_CHARS = 4096
DEFAULT_TIMEOUT_S = 20

WARNING = "⚠️ Resposta automática, sem verificação. Confira antes de citar."


class TelegramError(Exception):
    """Raised when Telegram rejects a message."""


def build_message(question: str, answer: str, book: str | None) -> str:
    lines = [f"❓ {question.strip()}", "", answer.strip(), ""]
    if book:
        lines.append(f"📖 {book}")
    lines.append(WARNING)
    return "\n".join(lines)


def _default_poster(url: str, data: dict, timeout: int):
    return httpx.post(url, data=data, timeout=timeout)


class TelegramSender:
    def __init__(
        self,
        token: str,
        chat_id: str,
        poster: Callable[[str, dict, int], object] = _default_poster,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ):
        self._token = token
        self._chat_id = chat_id
        self._poster = poster
        self._timeout_s = timeout_s

    def send(self, text: str, reply_to: int | None = None) -> int:
        """Send text, splitting at Telegram's per-message limit.

        Returns the message_id of the first chunk, so the caller can map a reply
        back to the note it belongs to.
        """
        chunks = [
            text[at : at + MAX_MESSAGE_CHARS] for at in range(0, len(text), MAX_MESSAGE_CHARS)
        ] or [""]
        url = f"{API_BASE}/bot{self._token}/sendMessage"

        first_message_id = 0
        for index, chunk in enumerate(chunks):
            data = {"chat_id": self._chat_id, "text": chunk}
            if reply_to and index == 0:
                data["reply_to_message_id"] = reply_to
            try:
                response = self._poster(url, data, self._timeout_s)
            except Exception as exc:  # httpx raises a family of transport errors
                # Never let the token reach a log line or an exception message.
                raise TelegramError(f"could not reach Telegram: {type(exc).__name__}") from exc

            status = getattr(response, "status_code", 0)
            if status != 200:
                raise TelegramError(f"Telegram returned HTTP {status}")
            payload = response.json()
            if not payload.get("ok"):
                raise TelegramError(
                    f"Telegram rejected the message: {payload.get('description', 'unknown')}"
                )
            if index == 0:
                first_message_id = int((payload.get("result") or {}).get("message_id", 0))
        return first_message_id

    def poll(self, offset: int, timeout_s: int = 25) -> list[dict]:
        """Long-poll for incoming updates. No public URL or webhook needed."""
        url = f"{API_BASE}/bot{self._token}/getUpdates"
        data = {"offset": offset, "timeout": timeout_s}
        try:
            response = self._poster(url, data, timeout_s + 10)
        except Exception as exc:
            raise TelegramError(f"could not reach Telegram: {type(exc).__name__}") from exc
        if getattr(response, "status_code", 0) != 200:
            raise TelegramError(f"Telegram returned HTTP {response.status_code}")
        payload = response.json()
        if not payload.get("ok"):
            raise TelegramError(f"Telegram rejected getUpdates: {payload.get('description')}")
        return list(payload.get("result") or [])
