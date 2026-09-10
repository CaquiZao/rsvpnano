"""Decide what files in a Drive folder are ready to become notes.

No network on purpose: these are the pairing and deadline rules, and keeping
them away from I/O is what puts them under test, for the same reason the
firmware keeps them in planFrom() on the SD card, not in the uploader.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    ready: list[ReadyNote] = []
    for wav in files:
        if not wav.name.lower().endswith(WAV_SUFFIX):
            continue
        if wav.id in processed_ids:
            continue
        note_id = wav.name[: -len(WAV_SUFFIX)]
        sidecar = sidecars.get(note_id)
        if sidecar is None and now - wav.created_at < grace:
            continue  # May be an upload in flight.
        ready.append(ReadyNote(note_id=note_id, wav=wav, sidecar=sidecar))

    ready.sort(key=lambda r: r.wav.created_at)
    return ready
