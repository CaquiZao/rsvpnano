"""Decide what files in a Drive folder are ready to become notes, and which
of them are only taking up room in the listing.

No network on purpose: these are the pairing and deadline rules, and keeping
them away from I/O is what puts them under test, for the same reason the
firmware keeps them in planFrom() on the SD card, not in the uploader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

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


@dataclass(frozen=True)
class InboxPlan:
    ready: list[ReadyNote]
    # Files nothing will ever read again, and which the caller must therefore
    # delete: the folder does not drain by itself. A best-effort delete that
    # failed, and an orphan sidecar that can never be paired, would otherwise
    # sit in the listing forever -- and a listing full of them is how a new
    # recording becomes invisible while the device has already dropped its
    # only copy.
    stale: list[RemoteFile]


def note_id_of(name: str) -> str:
    """The recording a Drive file name belongs to.

    `Path(...).stem` rather than a slice, the same defence POST /v1/notes takes
    on the uploaded file name: this value ends up interpolated into a path
    under `audio_store`, and a name the device never wrote (the folder is the
    user's own Drive) must not be able to point at another directory.
    """
    return Path(name).stem


def plan_inbox(
    files: list[RemoteFile],
    processed_ids: set[str],
    now: datetime,
    grace_s: int = DEFAULT_GRACE_S,
) -> InboxPlan:
    """What to turn into notes, oldest first, and what to delete."""
    sidecars = {
        note_id_of(f.name): f
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
        wavs_by_note.setdefault(note_id_of(f.name), []).append(f)

    ready: list[ReadyNote] = []
    stale: list[RemoteFile] = []
    for note_id, wavs in wavs_by_note.items():
        wavs.sort(key=lambda w: w.created_at)
        sidecar = sidecars.get(note_id)
        if note_id in processed_ids:
            # This recording is already a note -- delivered by an earlier poll
            # whose delete failed, or by POST /v1/notes, which records into the
            # same store. Skipping it (what this did before) left it in the
            # folder for good; reporting it is what drains the folder.
            stale.extend(wavs)
            if sidecar is not None:
                stale.append(sidecar)
            continue
        wav, *extra_copies = wavs
        if sidecar is None and now - wav.created_at < grace:
            continue  # May be an upload in flight.
        ready.append(ReadyNote(note_id=note_id, wav=wav, sidecar=sidecar, extra_copies=extra_copies))

    for note_id, sidecar in sidecars.items():
        if note_id in wavs_by_note:
            continue
        # A sidecar with no .wav of its own is never going to become a note.
        # Inside the grace period it could still be the half of a pair whose
        # audio is mid-upload, and deleting it there would cost that note its
        # anchor -- so it only becomes stale once the deadline has passed.
        if note_id not in processed_ids and now - sidecar.created_at < grace:
            continue
        stale.append(sidecar)

    ready.sort(key=lambda r: r.wav.created_at)
    stale.sort(key=lambda f: f.created_at)
    return InboxPlan(ready=ready, stale=stale)
