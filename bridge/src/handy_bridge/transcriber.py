"""Transcribe a WAV by shelling out to the Handy CLI.

Handy writes its logs to stderr and a single JSON object to stdout, and exits 0.
Only stdout is parsed.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from handy_bridge.config import AsrConfig


class TranscriptionError(Exception):
    """Raised when Handy fails or returns output the bridge cannot use."""


@dataclass(frozen=True)
class Transcription:
    text: str
    audio_secs: float
    rtf: float
    model: str


def _default_runner(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout
    )


def transcribe(
    wav: Path,
    cfg: AsrConfig,
    runner: Callable[[list[str], int], subprocess.CompletedProcess] = _default_runner,
) -> Transcription:
    cmd = [
        str(cfg.handy_exe),
        "--transcribe-file",
        str(wav),
        "--json",
        "--model",
        cfg.model,
    ]
    try:
        completed = runner(cmd, cfg.timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError(f"Handy timed out after {cfg.timeout_s}s") from exc
    except FileNotFoundError as exc:
        raise TranscriptionError(
            f"Handy executable not found: {cfg.handy_exe}"
        ) from exc

    if completed.returncode != 0:
        raise TranscriptionError(
            f"Handy failed with exit code {completed.returncode}: "
            f"{(completed.stderr or '').strip()[-300:]}"
        )

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TranscriptionError("Handy stdout was not valid JSON") from exc

    if "text" not in payload:
        raise TranscriptionError("Handy output has no 'text' field")

    return Transcription(
        text=str(payload["text"]).strip(),
        audio_secs=float(payload.get("audio_secs", 0.0)),
        rtf=float(payload.get("rtf", 0.0)),
        model=str(payload.get("model", cfg.model)),
    )
