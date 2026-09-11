"""Transcribe a WAV by shelling out to the Handy CLI.

Handy writes its logs to stderr and a single JSON object to stdout, and exits 0.
Only stdout is parsed.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from handy_bridge.config import AsrConfig

log = logging.getLogger(__name__)


class TranscriptionError(Exception):
    """Raised when Handy fails or returns output the bridge cannot use.

    `retry_elsewhere` says whether another compute device could plausibly do
    better. A dead process can: the buffer a GPU backend fails to allocate is
    the one a larger device has room for. A timeout cannot -- the next device
    down the list is slower, so it would only make the same wait longer.
    """

    def __init__(self, message: str, retry_elsewhere: bool = False):
        super().__init__(message)
        self.retry_elsewhere = retry_elsewhere


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
    """Transcribe on the first configured device that survives the attempt.

    The fallback exists because of a lost recording: a 1m48s note crashed the
    2GB GPU with a failed 1.1GB Vulkan allocation, and the same file transcribed
    in 33s on the iGPU. A device list makes the ceiling something the bridge
    steps over instead of something that eats notes.
    """
    devices: tuple[int | None, ...] = cfg.device_indexes or (None,)
    for attempt, device in enumerate(devices):
        try:
            return _transcribe_on(wav, cfg, device, runner)
        except TranscriptionError as exc:
            last = attempt == len(devices) - 1
            if last or not exc.retry_elsewhere:
                raise
            log.warning(
                "transcription failed on device %s (%s); trying device %s",
                device,
                exc,
                devices[attempt + 1],
            )
    raise AssertionError("unreachable: the loop either returns or raises")


def _transcribe_on(
    wav: Path,
    cfg: AsrConfig,
    device: int | None,
    runner: Callable[[list[str], int], subprocess.CompletedProcess],
) -> Transcription:
    cmd = [
        str(cfg.handy_exe),
        "--transcribe-file",
        str(wav),
        "--json",
        "--model",
        cfg.model,
    ]
    if device is not None:
        cmd += ["--device-index", str(device)]
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
            f"{(completed.stderr or '').strip()[-300:]}",
            retry_elsewhere=True,
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
