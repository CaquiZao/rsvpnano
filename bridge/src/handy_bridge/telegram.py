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
    """The answer to one question, as a reply under the note's arrival notice.

    An empty question renders answer-only, and the caller passes one when the
    question is just the whole recording again. That happens for a `pergunta`
    note where no specific question could be extracted: the fallback asks the
    model about the entire body, so echoing it here would reprint the transcript
    the arrival notice showed a second earlier.
    """
    lines = []
    if question.strip():
        lines += [f"❓ {question.strip()}", ""]
    lines += [answer.strip(), ""]
    if book:
        lines.append(f"📖 {book}")
    lines.append(WARNING)
    return "\n".join(lines)


# One line per kind, because "chegou uma nota" says less than the phone screen
# has room for. The label is also what tells you whether to wait for more.
KIND_HEADERS = {
    "anotação": "📝 Anotação",
    "pergunta": "❓ Pergunta",
    "recall": "🔁 Recall",
}
# A ten-minute recording is a wall of text on a phone. The note keeps the whole
# transcript; this message only has to be enough to recognise which note it is.
MAX_TRANSCRIPT_CHARS = 900


def build_arrival(
    kind: str,
    title: str,
    transcript: str,
    book: str | None = None,
    reasoning: str = "",
    deepening: str = "",
    answers_coming: int = 0,
) -> str:
    """Announce a note that just landed in the vault, with what it says.

    Deliberately without the LLM-cleaned body, even though the note has it. The
    cleaned version is the same words tidied -- `houveram` to `houve`, the spoken
    marker word removed -- so on a phone it reads as the transcript printed twice.
    The note gets away with carrying both because the raw one sits in a collapsed
    callout; a chat message has nowhere to collapse it to.

    What survives is what says something new: the title, the discussion of the
    thinking, and the words you actually spoke. The polish is one tap away in the
    vault.
    """
    header = KIND_HEADERS.get(kind.strip().lower(), "📝 Nota")
    lines = [f"{header} — {title.strip()}" if title.strip() else header, ""]

    for label, text in (("🧠 Seu raciocínio", reasoning), ("💡 Indo mais fundo", deepening)):
        if text.strip():
            lines += [f"{label}: {text.strip()}", ""]

    spoken = transcript.strip()
    if spoken:
        if len(spoken) > MAX_TRANSCRIPT_CHARS:
            spoken = spoken[:MAX_TRANSCRIPT_CHARS].rstrip() + "… (transcrição cortada)"
        lines += [f"🎙️ {spoken}", ""]

    if book:
        lines.append(f"📖 {book}")
    if answers_coming:
        plural = "s" if answers_coming > 1 else ""
        lines.append(f"⏳ {answers_coming} resposta{plural} chegando em seguida.")
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
