"""Decide what files in a Drive folder are ready to become notes.

No network on purpose: these are the pairing and deadline rules, and keeping
them away from I/O is what puts them under test, for the same reason the
firmware keeps them in planFrom() on the SD card, not in the uploader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

WAV_SUFFIX = ".wav"
SIDECAR_SUFFIX = ".json"
# The device uploads the .wav and then the .json, so a .wav alone may be
# an upload in flight. Five minutes comfortably outlasts the worst plausible
# upload of a ten-minute note, and after that the sidecar is not coming.
DEFAULT_GRACE_S = 300


@dataclass(frozen=True)
class RemoteFile:
    id: str
    name: str
    created_at: datetime


@dataclass(frozen=True)
class ReadyNote:
    note_id: str
    wav: RemoteFile
    sidecar: RemoteFile | None
    # A retry after a failed sidecar re-uploads the .wav under a new file id,
    # so the same stem can show up more than once in a single listing. These
    # are the losers of that race: the caller must delete them, not turn them
    # into notes of their own.
    extra_copies: list[RemoteFile] = field(default_factory=list)


def plan_inbox(
    files: list[RemoteFile],
    processed_ids: set[str],
    now: datetime,
    grace_s: int = DEFAULT_GRACE_S,
) -> list[ReadyNote]:
    """Notes ready to process, oldest first."""
    sidecars = {
        f.name[: -len(SIDECAR_SUFFIX)]: f
        for f in files
        if f.name.lower().endswith(SIDECAR_SUFFIX)
    }
    grace = timedelta(seconds=grace_s)

    # Grouped by note_id (the stem), not by file id: Drive mints a new id on
    # every files.create and never enforces unique names, so a retried
    # upload leaves two .wav with different ids but the same stem. The stem
    # is the only thing that identifies the recording -- it is what "already
    # processed" and "already offered" both have to mean.
    wavs_by_note: dict[str, list[RemoteFile]] = {}
    for f in files:
        if not f.name.lower().endswith(WAV_SUFFIX):
            continue
        wavs_by_note.setdefault(f.name[: -len(WAV_SUFFIX)], []).append(f)

    ready: list[ReadyNote] = []
    for note_id, wavs in wavs_by_note.items():
        if note_id in processed_ids:
            continue
        wavs.sort(key=lambda w: w.created_at)
        wav, *extra_copies = wavs
        sidecar = sidecars.get(note_id)
        if sidecar is None and now - wav.created_at < grace:
            continue  # May be an upload in flight.
        ready.append(ReadyNote(note_id=note_id, wav=wav, sidecar=sidecar, extra_copies=extra_copies))

    ready.sort(key=lambda r: r.wav.created_at)
    return ready
