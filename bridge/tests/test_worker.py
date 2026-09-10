import struct
from pathlib import Path

from handy_bridge.config import AsrConfig, Config, PostProcessConfig
from handy_bridge.pipeline import IncomingNote
from handy_bridge.transcriber import Transcription, TranscriptionError
from handy_bridge.worker import NoteWorker


def make_cfg(tmp_path) -> Config:
    vault = tmp_path / "Reading"
    vault.mkdir(exist_ok=True)
    return Config(
        vault_path=vault,
        inbox_folder="Inbox",
        audio_store=tmp_path / "audio",
        port=8787,
        asr=AsrConfig(handy_exe=tmp_path / "handy.exe", model="m.gguf", timeout_s=900),
        post_process=PostProcessConfig(enabled=False, backend="none", model=""),
    )


def make_wav(path: Path) -> Path:
    data = b"\x00\x01" * 16000
    path.write_bytes(
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    return path


def test_processes_submitted_notes(tmp_path):
    cfg = make_cfg(tmp_path)
    worker = NoteWorker(
        cfg,
        processor=None,
        transcribe_fn=lambda wav, c: Transcription("ola", 1.0, 1.3, "m.gguf"),
    )
    worker.start()
    worker.submit(
        IncomingNote(
            "n1",
            make_wav(tmp_path / "n1.wav"),
            {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
        )
    )
    worker.stop(timeout=10)

    # These notes carry no book, so they land in the general folder rather than
    # loose in the inbox: one subfolder per book keeps parallel readings apart.
    notes = list((cfg.vault_path / "Geral" / "Anotações").glob("*.md"))
    assert len(notes) == 1
    assert "ola" in notes[0].read_text(encoding="utf-8")


def test_one_failure_does_not_kill_the_worker(tmp_path):
    cfg = make_cfg(tmp_path)
    calls = {"n": 0}

    def flaky(wav, c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TranscriptionError("first one explodes")
        return Transcription("segunda", 1.0, 1.3, "m.gguf")

    worker = NoteWorker(cfg, processor=None, transcribe_fn=flaky)
    worker.start()
    worker.submit(
        IncomingNote("n1", make_wav(tmp_path / "n1.wav"), {"clock_synced": False})
    )
    worker.submit(
        IncomingNote("n2", make_wav(tmp_path / "n2.wav"), {"clock_synced": False})
    )
    worker.stop(timeout=10)

    # These notes carry no book, so they land in the general folder rather than
    # loose in the inbox: one subfolder per book keeps parallel readings apart.
    notes = list((cfg.vault_path / "Geral" / "Anotações").glob("*.md"))
    assert len(notes) == 1
    assert "segunda" in notes[0].read_text(encoding="utf-8")
