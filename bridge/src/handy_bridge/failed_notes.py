"""Recordings that reached the bridge and did not become notes.

This exists because of a lost note. A 1m48s recording arrived over the Drive
fallback, crashed the GPU during transcription, and the worker logged the
exception and moved on -- so the note never existed, nothing retried it, and
nothing said so. The copy on Drive had already been deleted, and the note_id
was already in the processed list, which is correct (it is what stops the same
recording becoming two notes) but left no way back.

A parked note is the way back: the recording, the metadata it arrived with, and
the reason it failed, sitting together where the next start of the bridge will
find them. Retries are bounded, because a recording that is simply unusable
should be given up on loudly rather than re-run forever -- but the WAV is never
deleted, since it is the one part of a note nobody can reconstruct.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from handy_bridge.pipeline import IncomingNote

log = logging.getLogger(__name__)

FAILED_DIR = "failed"
# Three, so a transient failure (a GPU busy with something else, a model file
# still being written) gets past it, and a real one stops costing time.
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class ParkedNote:
    note_id: str
    wav_path: Path
    meta: dict
    attempts: int
    reason: str


def _dir(audio_store: Path) -> Path:
    return audio_store / FAILED_DIR


def park(audio_store: Path, incoming: IncomingNote, reason: str) -> int:
    """Keep the recording and why it failed. Returns how many tries it has had.

    The WAV moves out of the live store so the two states cannot be confused:
    a file in the store is a note that worked, a file in here is one that owes
    another attempt.
    """
    kept = _dir(audio_store)
    kept.mkdir(parents=True, exist_ok=True)

    record_path = kept / f"{incoming.note_id}.json"
    attempts = 1
    if record_path.exists():
        try:
            attempts = int(json.loads(record_path.read_text(encoding="utf-8"))["attempts"]) + 1
        except (ValueError, OSError, KeyError, TypeError):
            log.warning("registro ilegível em %s; contando como primeira tentativa", record_path)

    if incoming.wav_path.exists():
        incoming.wav_path.replace(kept / f"{incoming.note_id}.wav")
    else:
        # Worth recording even so: the reason is evidence, and `parked` will not
        # offer a record whose recording is missing.
        log.warning("nota %s falhou e o áudio não está em %s", incoming.note_id, incoming.wav_path)

    record_path.write_text(
        json.dumps(
            {
                "note_id": incoming.note_id,
                "meta": incoming.meta,
                "attempts": attempts,
                "reason": reason,
                "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return attempts


def parked(audio_store: Path) -> list[ParkedNote]:
    """Recordings still owed an attempt, oldest failure first."""
    kept = _dir(audio_store)
    if not kept.is_dir():
        return []

    out: list[tuple[str, ParkedNote]] = []
    for record_path in sorted(kept.glob("*.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            note_id = str(record["note_id"])
            attempts = int(record["attempts"])
        except (ValueError, OSError, KeyError, TypeError):
            # One unreadable record must not hide the others.
            log.warning("registro de falha ilegível: %s", record_path)
            continue

        wav = kept / f"{note_id}.wav"
        if attempts >= MAX_ATTEMPTS:
            log.warning(
                "nota %s falhou %d vezes; o áudio segue em %s e não será tentado de novo",
                note_id,
                attempts,
                wav,
            )
            continue
        if not wav.exists():
            log.warning("registro de falha %s sem áudio em %s", note_id, wav)
            continue

        meta = record.get("meta")
        out.append(
            (
                str(record.get("failed_at", "")),
                ParkedNote(
                    note_id=note_id,
                    wav_path=wav,
                    meta=meta if isinstance(meta, dict) else {},
                    attempts=attempts,
                    reason=str(record.get("reason", "")),
                ),
            )
        )
    return [note for _, note in sorted(out, key=lambda pair: pair[0])]


def release(audio_store: Path, note: ParkedNote) -> IncomingNote:
    """Move the recording back into the store and hand it over for another try.

    The record is left behind on purpose: it carries the attempt count, and
    only the note actually landing (`forget`) should clear it.
    """
    audio_store.mkdir(parents=True, exist_ok=True)
    target = audio_store / f"{note.note_id}.wav"
    note.wav_path.replace(target)
    return IncomingNote(note_id=note.note_id, wav_path=target, meta=note.meta)


def forget(audio_store: Path, note_id: str) -> None:
    """Drop the record once the note exists. A no-op for a note that never failed."""
    (_dir(audio_store) / f"{note_id}.json").unlink(missing_ok=True)
