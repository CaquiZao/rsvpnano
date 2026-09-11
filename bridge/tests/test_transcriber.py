import json
import subprocess

import pytest

from handy_bridge.config import AsrConfig
from handy_bridge.transcriber import Transcription, TranscriptionError, transcribe


def cfg(tmp_path, devices=()) -> AsrConfig:
    return AsrConfig(
        handy_exe=tmp_path / "handy.exe",
        model="m/model.gguf",
        timeout_s=900,
        device_indexes=devices,
    )


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


def test_no_device_flag_when_none_configured(tmp_path):
    """An unset device list keeps Handy picking for itself, as it always did."""
    seen = {}

    def runner(cmd, timeout):
        seen["cmd"] = cmd
        return FakeCompleted(HANDY_OUT)

    transcribe(tmp_path / "a.wav", cfg(tmp_path), runner=runner)
    assert "--device-index" not in seen["cmd"]


def test_first_device_is_asked_first(tmp_path):
    seen = []

    def runner(cmd, timeout):
        seen.append(cmd)
        return FakeCompleted(HANDY_OUT)

    transcribe(tmp_path / "a.wav", cfg(tmp_path, devices=(1, 2)), runner=runner)
    assert len(seen) == 1
    assert seen[0][seen[0].index("--device-index") + 1] == "1"


def test_a_crashed_device_falls_to_the_next(tmp_path):
    """The real failure: the 2GB GPU dies on a long recording, the 6GB one does not.

    Exit 3221225477 is the Windows access violation ggml leaves behind when a
    Vulkan buffer allocation fails, so there is no tidy 'out of memory' to match
    on -- any dead process is reason enough to ask the next device.
    """
    seen = []

    def runner(cmd, timeout):
        seen.append(cmd[cmd.index("--device-index") + 1])
        if seen[-1] == "0":
            return FakeCompleted("", 3221225477, "ggml_vulkan: Device memory allocation failed")
        return FakeCompleted(HANDY_OUT)

    got = transcribe(tmp_path / "a.wav", cfg(tmp_path, devices=(0, 1)), runner=runner)
    assert got.text == "ola mundo"
    assert seen == ["0", "1"]


def test_every_device_failing_raises_the_last_error(tmp_path):
    with pytest.raises(TranscriptionError, match="exit code 3221225477"):
        transcribe(
            tmp_path / "a.wav",
            cfg(tmp_path, devices=(0, 1)),
            runner=lambda cmd, timeout: FakeCompleted("", 3221225477, "boom"),
        )


def test_a_timeout_does_not_try_another_device(tmp_path):
    """A slower device would only time out later. One wait is the whole budget."""
    seen = []

    def runner(cmd, timeout):
        seen.append(cmd)
        raise subprocess.TimeoutExpired(cmd, timeout)

    with pytest.raises(TranscriptionError, match="timed out"):
        transcribe(tmp_path / "a.wav", cfg(tmp_path, devices=(0, 1, 2)), runner=runner)
    assert len(seen) == 1
