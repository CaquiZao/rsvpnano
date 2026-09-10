"""HTTP surface: accept a recording, persist it, acknowledge immediately."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from handy_bridge import wav as wav_mod
from handy_bridge.config import Config
from handy_bridge.pipeline import IncomingNote

log = logging.getLogger(__name__)

MIN_AUDIO_SECONDS = 1.0
# Where a refused recording goes instead of being deleted.
REJECTED_DIR = "rejected"


def preserve_rejected(target: Path, note_id: str, reason: str, meta: str | None = None) -> None:
    """Keep the audio of a refused recording, and say why somewhere someone reads.

    A refusal is precisely when the recording is most worth keeping: whichever
    route brought it, the device is done with it -- it treats a 4xx as final and
    deletes its own copy, and a note that came by Drive has already had its copy
    removed from the folder -- so what lands here is the last one there is.

    Shared by both entrances on purpose. A recording refused by one route and
    silently turned into a broken note by the other is the divergence this
    function exists to prevent.
    """
    kept = target.parent / REJECTED_DIR
    kept.mkdir(parents=True, exist_ok=True)
    target.replace(kept / target.name)
    # The sidecar only when the sidecar is what broke. It is written by the
    # firmware, so its exact bytes are the bug report -- "not valid JSON" alone
    # says which layer failed and nothing about how.
    if meta is not None:
        (kept / f"{note_id}.meta.txt").write_text(meta, encoding="utf-8")
    log.warning("refused note %s: %s (audio kept in %s)", note_id, reason, REJECTED_DIR)


def refuse(target: Path, note_id: str, reason: str, meta: str | None = None) -> JSONResponse:
    """Answer 400 and keep the audio.

    Both halves matter and both were missing. The device reads the status line
    and nothing else -- draining the body would cost radio time -- so a reason
    that only travels in the response reaches no one at all.
    """
    preserve_rejected(target, note_id, reason, meta=meta)
    return JSONResponse({"error": reason}, status_code=400)


class RemembersNothing:
    """The default note_id store: accepts everything, keeps nothing.

    Only the Drive route reads the store, so a bridge with the fallback
    switched off has nothing to remember. It exists so this endpoint does not
    have to ask whether a store is configured on every request.
    """

    def add(self, note_id: str) -> None:
        return None


def create_app(
    cfg: Config,
    submit: Callable[[IncomingNote], None] | None = None,
    processed: object | None = None,
) -> FastAPI:
    app = FastAPI(title="handy-bridge")
    hand_off = submit or (lambda incoming: None)
    # The same ProcessedIds the Drive poller reads. One recording can reach the
    # bridge through both doors: the device confirms the WAV on Drive, fails on
    # the sidecar, keeps the entry queued, and the next flush finds the bridge
    # on the LAN and delivers here. Without this line, the copy left on Drive
    # outlives the grace period and the poller writes the same recording a
    # second time -- and notes are source, written once and never reconciled.
    remember = processed if processed is not None else RemembersNothing()

    def describe(request: Request) -> str:
        return (
            f"content-type={request.headers.get('content-type', '?')}, "
            f"declared-length={request.headers.get('content-length', '?')}"
        )

    # Two refusals happen before the endpoint ever runs: Starlette rejects a body it
    # cannot parse, and FastAPI rejects a form with a field missing. Both answer 4xx,
    # and the device deletes its only copy of the recording over any 4xx -- so a bare
    # status code in the access log is a note lost with no way to ask why. Framing is
    # exactly what breaks here, so the request's own shape is what gets logged.
    @app.exception_handler(StarletteHTTPException)
    async def log_http_refusal(request: Request, exc: StarletteHTTPException) -> Response:
        log.warning("refused %s %s: %s (%s)", request.method, request.url.path, exc.detail,
                    describe(request))
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def log_validation_refusal(request: Request, exc: RequestValidationError) -> Response:
        fields = ", ".join(".".join(str(part) for part in err["loc"]) for err in exc.errors())
        log.warning("refused %s %s: unusable fields [%s] (%s)", request.method, request.url.path,
                    fields, describe(request))
        return await request_validation_exception_handler(request, exc)

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/notes")
    async def receive_note(audio: UploadFile, meta: str = Form(...)) -> JSONResponse:
        note_id = Path(audio.filename or "note").stem
        cfg.audio_store.mkdir(parents=True, exist_ok=True)
        target = cfg.audio_store / f"{note_id}.wav"

        # On disk before anything is validated, including the sidecar: the audio is
        # the one part of this request nobody can reconstruct, and every check below
        # is a reason to hold on to it rather than a reason to drop it.
        target.write_bytes(await audio.read())

        try:
            parsed_meta = json.loads(meta)
        except json.JSONDecodeError:
            return refuse(target, note_id, "meta is not valid JSON", meta=meta)

        try:
            info = wav_mod.inspect(target)
        except wav_mod.InvalidWav as exc:
            return refuse(target, note_id, f"invalid WAV: {exc}")

        if info.duration_s < MIN_AUDIO_SECONDS:
            return refuse(target, note_id, f"audio too short: {info.duration_s:.2f}s")

        # Acknowledge as soon as the audio is safely on disk; transcription is async
        # so the device can drop its radio instead of waiting on inference.
        hand_off(IncomingNote(note_id=note_id, wav_path=target, meta=parsed_meta))
        # After the hand-off, not before: this records that the note exists, and
        # it only exists once the worker owns it.
        remember.add(note_id)
        log.info("accepted note %s (%.1fs)", note_id, info.duration_s)
        return JSONResponse({"id": note_id, "status": "accepted"}, status_code=200)

    return app
