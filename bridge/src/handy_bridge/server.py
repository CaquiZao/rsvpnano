"""HTTP surface: accept a recording, persist it, acknowledge immediately."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import JSONResponse

from handy_bridge import wav as wav_mod
from handy_bridge.config import Config
from handy_bridge.pipeline import IncomingNote

log = logging.getLogger(__name__)

MIN_AUDIO_SECONDS = 1.0


def create_app(cfg: Config, submit: Callable[[IncomingNote], None] | None = None) -> FastAPI:
    app = FastAPI(title="handy-bridge")
    hand_off = submit or (lambda incoming: None)

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/notes")
    async def receive_note(audio: UploadFile, meta: str = Form(...)) -> JSONResponse:
        try:
            parsed_meta = json.loads(meta)
        except json.JSONDecodeError:
            return JSONResponse({"error": "meta is not valid JSON"}, status_code=400)

        note_id = Path(audio.filename or "note").stem
        cfg.audio_store.mkdir(parents=True, exist_ok=True)
        target = cfg.audio_store / f"{note_id}.wav"
        target.write_bytes(await audio.read())

        try:
            info = wav_mod.inspect(target)
        except wav_mod.InvalidWav as exc:
            target.unlink(missing_ok=True)
            return JSONResponse({"error": f"invalid WAV: {exc}"}, status_code=400)

        if info.duration_s < MIN_AUDIO_SECONDS:
            target.unlink(missing_ok=True)
            return JSONResponse(
                {"error": f"audio too short: {info.duration_s:.2f}s"}, status_code=400
            )

        # Acknowledge as soon as the audio is safely on disk; transcription is async
        # so the device can drop its radio instead of waiting on inference.
        hand_off(IncomingNote(note_id=note_id, wav_path=target, meta=parsed_meta))
        log.info("accepted note %s (%.1fs)", note_id, info.duration_s)
        return JSONResponse({"id": note_id, "status": "accepted"}, status_code=200)

    return app
