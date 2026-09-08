"""Serialize note processing on a single background thread.

Transcription is GPU-bound and the device may upload a burst after being offline,
so notes are handled one at a time rather than concurrently.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from handy_bridge.config import Config
from handy_bridge.pipeline import IncomingNote, process_note
from handy_bridge.postprocess import PostProcessor
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
    ):
        self._cfg = cfg
        self._processor = processor
        self._transcribe_fn = transcribe_fn
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
                )
                log.info("wrote note %s -> %s", item.note_id, path.name)
            except Exception:
                # One bad recording must not take the worker down with it.
                log.exception("failed to process note %s", item.note_id)
