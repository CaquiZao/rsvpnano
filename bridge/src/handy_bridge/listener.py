"""Answer Telegram follow-ups so a first answer is never a dead end.

Uses long polling rather than a webhook: a webhook would need a public HTTPS
endpoint, which a home machine does not have without a tunnel.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from handy_bridge.note import append_followup
from handy_bridge.postprocess import PostProcessError, PostProcessor
from handy_bridge.threads import ThreadStore

log = logging.getLogger(__name__)

FAILURE_REPLY = "Não consegui responder agora. Tente de novo em alguns instantes."


class TelegramListener:
    def __init__(
        self,
        telegram,
        processor: PostProcessor,
        store: ThreadStore,
        allowed_chat_id: str,
        poll_timeout_s: int = 25,
    ):
        self._telegram = telegram
        self._processor = processor
        self._store = store
        self._allowed_chat_id = str(allowed_chat_id)
        self._poll_timeout_s = poll_timeout_s
        self._offset = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # --- ciclo de vida ---------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="telegram-listener", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                # A transport hiccup must not kill the listener for the session.
                log.exception("Telegram polling failed; will retry")
                self._stop.wait(10)

    # --- trabalho --------------------------------------------------------

    def poll_once(self) -> int:
        updates = self._telegram.poll(self._offset, self._poll_timeout_s)
        handled = 0
        for update in updates:
            self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
            if self._handle(update.get("message") or update.get("edited_message") or {}):
                handled += 1
        return handled

    def _handle(self, message: dict) -> bool:
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != self._allowed_chat_id:
            # The bot's username is public, so an unfiltered bot would let a stranger
            # spend the owner's model usage.
            log.warning("ignoring a message from chat %s", chat_id or "?")
            return False

        question = (message.get("text") or "").strip()
        if not question:
            return False

        replied_to = (message.get("reply_to_message") or {}).get("message_id")
        thread = self._store.resolve(int(replied_to)) if replied_to else self._store.latest()
        history = thread.history if thread else []
        note_path: Path | None = thread.note_path if thread else None
        excerpt = None

        try:
            answer = self._processor.answer_followup(question, history, excerpt)
        except PostProcessError as exc:
            log.warning("could not answer a follow-up: %s", exc)
            self._telegram.send(FAILURE_REPLY, reply_to=message.get("message_id"))
            return False

        sent_id = self._telegram.send(answer, reply_to=message.get("message_id"))

        if note_path is not None:
            try:
                append_followup(note_path, question, answer)
            except OSError as exc:
                # The answer already reached the phone; losing the record is the
                # lesser failure, so it only warns.
                log.warning("could not record the follow-up in %s: %s", note_path, exc)
            anchor = int(replied_to) if replied_to else self._store.latest_anchor()
            if anchor is not None:
                self._store.append(anchor, question, answer, new_message_id=sent_id)
        return True
