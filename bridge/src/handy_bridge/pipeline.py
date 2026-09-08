"""Orchestrate a single note: repair, transcribe, post-process, write."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from handy_bridge import wav as wav_mod
from handy_bridge.config import AsrConfig, Config
from handy_bridge.note import NoteData, write_note
from handy_bridge.postprocess import PostProcessError, PostProcessor
from handy_bridge.transcriber import Transcription
from handy_bridge.transcriber import transcribe as default_transcribe

log = logging.getLogger(__name__)

EMPTY_BODY = "(transcrição vazia)"


@dataclass(frozen=True)
class IncomingNote:
    note_id: str
    wav_path: Path
    meta: dict


def resolve_recorded_at(meta: dict, arrived_at: datetime) -> tuple[datetime, bool]:
    """Return (timestamp, was_estimated).

    The board has no battery-backed RTC, so a recording made offline after a reboot
    carries no wall-clock time. In that case the device sends monotonic uptime and
    the bridge reconstructs the moment from when the upload arrived.
    """
    if meta.get("clock_synced"):
        raw = meta.get("recorded_at")
        if raw:
            try:
                return datetime.fromisoformat(str(raw)), False
            except ValueError:
                log.warning("unparseable recorded_at %r; falling back to arrival", raw)
        return arrived_at, True

    recorded_uptime = meta.get("uptime_ms")
    upload_uptime = meta.get("uptime_at_upload_ms")
    if recorded_uptime is not None and upload_uptime is not None:
        delta_ms = float(upload_uptime) - float(recorded_uptime)
        if delta_ms >= 0:
            return arrived_at - timedelta(milliseconds=delta_ms), True
    return arrived_at, True


def process_note(
    incoming: IncomingNote,
    cfg: Config,
    *,
    transcribe_fn: Callable[[Path, AsrConfig], Transcription] = default_transcribe,
    processor: PostProcessor | None = None,
    now: Callable[[], datetime] = datetime.now,
) -> Path:
    arrived_at = now()

    try:
        wav_mod.repair_header(incoming.wav_path)
    except wav_mod.InvalidWav:
        log.warning("could not repair %s; transcribing as-is", incoming.wav_path)
    info = wav_mod.inspect(incoming.wav_path)

    transcription = transcribe_fn(incoming.wav_path, cfg.asr)
    raw_text = transcription.text.strip()

    recorded_at, estimated = resolve_recorded_at(incoming.meta, arrived_at)
    title = f"{recorded_at:%Y-%m-%d %H%M}"
    tags: list[str] = []
    body = raw_text or EMPTY_BODY

    if processor is not None and raw_text:
        try:
            result = processor.process(raw_text)
            title = result.title or title
            tags = result.tags
            body = result.cleaned or raw_text
        except PostProcessError as exc:
            # A post-processing failure must never cost a note.
            log.warning("post-processing failed for %s: %s", incoming.note_id, exc)

    word_offset = incoming.meta.get("word_offset")
    return write_note(
        cfg.inbox_path,
        NoteData(
            title=title,
            tags=tags,
            body=body,
            raw_transcript=raw_text,
            recorded_at=recorded_at,
            date_estimated=estimated,
            duration_s=info.duration_s,
            asr_model=transcription.model,
            # Anchor fields arrive only when the device recorded from the reader.
            book=incoming.meta.get("book") or None,
            word_offset=int(word_offset) if word_offset is not None else None,
            excerpt=incoming.meta.get("excerpt") or None,
        ),
    )
