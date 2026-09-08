import json

import pytest

from handy_bridge.config import AsrConfig
from handy_bridge.transcriber import Transcription, TranscriptionError, transcribe


def cfg(tmp_path) -> AsrConfig:
    return AsrConfig(handy_exe=tmp_path / "handy.exe", model="m/model.gguf", timeout_s=900)


class FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


HANDY_OUT = json.dumps(
    {
        "audio_secs": 2.0,
        "best_ms": 1508,
        "load_ms": 1366,
        "model": "m/model.gguf",
        "rtf": 1.326,
        "text": "ola mundo",
        "transcribe_ms": [1508],
    }
)


def test_parses_stdout(tmp_path):
    got = transcribe(
        tmp_path / "a.wav", cfg(tmp_path), runner=lambda cmd, timeout: FakeCompleted(HANDY_OUT)
    )
    assert got == Transcription(
        text="ola mundo", audio_secs=2.0, rtf=1.326, model="m/model.gguf"
    )


def test_ignores_stderr_logs(tmp_path):
    noisy = FakeCompleted(HANDY_OUT, stderr="[INFO] loaded Vulkan backend\n[INFO] done\n")
    assert (
        transcribe(
            tmp_path / "a.wav", cfg(tmp_path), runner=lambda cmd, timeout: noisy
        ).text
        == "ola mundo"
    )


def test_builds_expected_command(tmp_path):
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"], seen["timeout"] = cmd, timeout
        return FakeCompleted(HANDY_OUT)

    wav = tmp_path / "note.wav"
    transcribe(wav, cfg(tmp_path), runner=runner)
    assert seen["cmd"][0] == str(tmp_path / "handy.exe")
    assert "--transcribe-file" in seen["cmd"] and str(wav) in seen["cmd"]
    assert "--json" in seen["cmd"]
    assert "--model" in seen["cmd"] and "m/model.gguf" in seen["cmd"]
    assert seen["timeout"] == 900


def test_empty_text_is_allowed(tmp_path):
    out = json.dumps({"audio_secs": 1.0, "rtf": 1.0, "model": "m", "text": ""})
    assert (
        transcribe(
            tmp_path / "a.wav", cfg(tmp_path), runner=lambda cmd, timeout: FakeCompleted(out)
        ).text
        == ""
    )


def test_raises_on_nonzero_exit(tmp_path):
    with pytest.raises(TranscriptionError, match="exit code 2"):
        transcribe(
            tmp_path / "a.wav",
            cfg(tmp_path),
            runner=lambda cmd, timeout: FakeCompleted("", 2, "model not installed"),
        )


def test_raises_on_unparseable_stdout(tmp_path):
    with pytest.raises(TranscriptionError, match="JSON"):
        transcribe(
            tmp_path / "a.wav",
            cfg(tmp_path),
            runner=lambda cmd, timeout: FakeCompleted("not json"),
        )


def test_raises_when_text_key_absent(tmp_path):
    with pytest.raises(TranscriptionError, match="text"):
        transcribe(
            tmp_path / "a.wav",
            cfg(tmp_path),
            runner=lambda cmd, timeout: FakeCompleted('{"audio_secs": 1.0}'),
        )
