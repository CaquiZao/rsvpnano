"""Serialize note processing on a single background thread.

Transcription is GPU-bound and the device may upload a burst after being offline,
so notes are handled one at a time rather than concurrently.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from handy_bridge import failed_notes
from handy_bridge.config import Config
from handy_bridge.pipeline import IncomingNote, process_note
from handy_bridge.postprocess import PostProcessor
from handy_bridge.telegram import build_failure
from handy_bridge.transcriber import Transcription
from handy_bridge.transcriber import transcribe as default_transcribe

log = logging.getLogger(__name__)

_SHUTDOWN = object()


class NoteWorker:
    def __init__(
        self,
        cfg: Config,
        processor: PostProcessor | None,
        transcribe_fn: Callable[..., Transcription] = default_transcribe,
        telegram: object | None = None,
        threads: object | None = None,
    ):
        self._cfg = cfg
        self._processor = processor
        self._transcribe_fn = transcribe_fn
        self._telegram = telegram
        self._threads = threads
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="note-worker", daemon=True
        )
        self._thread.start()

    def submit(self, incoming: IncomingNote) -> None:
        self._queue.put(incoming)

    def stop(self, timeout: float = 30.0) -> None:
        self._queue.put(_SHUTDOWN)
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _park(self, item: IncomingNote, exc: BaseException) -> None:
        """Keep the recording, then say so. In that order, and never raising.

        Telling the user is the part allowed to fail: a phone out of signal must
        not cost the recording the parking just saved.
        """
        try:
            attempts = failed_notes.park(self._cfg.audio_store, item, str(exc))
        except OSError:
            log.exception("could not park note %s; audio is at %s", item.note_id, item.wav_path)
            return

        if self._telegram is None:
            return
        try:
            self._telegram.send(
                build_failure(
                    item.note_id,
                    str(exc),
                    attempts,
                    will_retry=attempts < failed_notes.MAX_ATTEMPTS,
                )
            )
        except Exception as send_exc:  # noqa: BLE001 - warning about a failure is best-effort
            log.warning("could not warn about %s over Telegram: %s", item.note_id, send_exc)

    def retry_parked(self) -> int:
        """Hand every recording still owed an attempt back to the queue.

        Called at start-up: a transcription that failed because the GPU was out
        of memory usually succeeds on the next run, which now asks a different
        device. Returns how many were queued.
        """
        waiting = failed_notes.parked(self._cfg.audio_store)
        for note in waiting:
            log.info(
                "retentando nota %s (tentativa %d, falhou com: %s)",
                note.note_id,
                note.attempts + 1,
                note.reason,
            )
            self.submit(failed_notes.release(self._cfg.audio_store, note))
        return len(waiting)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SHUTDOWN:
                return
            try:
                path = process_note(
                    item,
                    self._cfg,
                    transcribe_fn=self._transcribe_fn,
                    processor=self._processor,
                    telegram=self._telegram,
                    threads=self._threads,
                )
                log.info("wrote note %s -> %s", item.note_id, path.name)
                # The note exists, so any earlier failure of this recording is
                # settled. A no-op for the overwhelming majority that never failed.
                failed_notes.forget(self._cfg.audio_store, item.note_id)
            except Exception as exc:
                # One bad recording must not take the worker down with it -- and
                # must not vanish either, which is what this used to do: the
                # exception was logged, the recording was left in a folder nobody
                # looks at, the copy on Drive had already been deleted, and no
                # note ever appeared. Park it so the next start tries again.
                log.exception("failed to process note %s", item.note_id)
                self._park(item, exc)
