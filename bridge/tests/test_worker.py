import struct
from pathlib import Path

from handy_bridge import failed_notes
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
    path.parent.mkdir(parents=True, exist_ok=True)
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


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text, reply_to=None):
        self.sent.append(text)
        return len(self.sent)


def test_a_failed_recording_is_parked_not_lost(tmp_path):
    """The bug this exists for: the exception was logged and the note vanished."""
    cfg = make_cfg(tmp_path)
    worker = NoteWorker(
        cfg,
        processor=None,
        transcribe_fn=lambda wav, c: (_ for _ in ()).throw(
            TranscriptionError("Handy failed with exit code 3221225477")
        ),
    )
    worker.start()
    worker.submit(
        IncomingNote("n1", make_wav(cfg.audio_store / "n1.wav"), {"book": "Sapiens"})
    )
    worker.stop(timeout=10)

    waiting = failed_notes.parked(cfg.audio_store)
    assert [n.note_id for n in waiting] == ["n1"]
    assert waiting[0].meta == {"book": "Sapiens"}
    assert "3221225477" in waiting[0].reason


def test_a_failed_recording_says_so_on_telegram(tmp_path):
    cfg = make_cfg(tmp_path)
    telegram = FakeTelegram()
    worker = NoteWorker(
        cfg,
        processor=None,
        transcribe_fn=lambda wav, c: (_ for _ in ()).throw(TranscriptionError("sem VRAM")),
        telegram=telegram,
    )
    worker.start()
    worker.submit(IncomingNote("n1", make_wav(cfg.audio_store / "n1.wav"), {}))
    worker.stop(timeout=10)

    assert len(telegram.sent) == 1
    assert "sem VRAM" in telegram.sent[0]


def test_a_telegram_that_is_down_does_not_lose_the_parked_note(tmp_path):
    """Warning about the failure must never become a second way to lose it."""
    cfg = make_cfg(tmp_path)

    class Broken:
        def send(self, text, reply_to=None):
            raise RuntimeError("no network")

    worker = NoteWorker(
        cfg,
        processor=None,
        transcribe_fn=lambda wav, c: (_ for _ in ()).throw(TranscriptionError("boom")),
        telegram=Broken(),
    )
    worker.start()
    worker.submit(IncomingNote("n1", make_wav(cfg.audio_store / "n1.wav"), {}))
    worker.stop(timeout=10)

    assert [n.note_id for n in failed_notes.parked(cfg.audio_store)] == ["n1"]


def test_a_note_that_lands_clears_its_old_failure(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.audio_store.mkdir(parents=True, exist_ok=True)
    failed_notes.park(
        cfg.audio_store,
        IncomingNote("n1", make_wav(cfg.audio_store / "n1.wav"), {}),
        "an earlier crash",
    )

    worker = NoteWorker(
        cfg,
        processor=None,
        transcribe_fn=lambda wav, c: Transcription("agora foi", 1.0, 1.3, "m.gguf"),
    )
    worker.start()
    worker.submit(
        IncomingNote(
            "n1",
            failed_notes.release(cfg.audio_store, failed_notes.parked(cfg.audio_store)[0]).wav_path,
            {},
        )
    )
    worker.stop(timeout=10)

    assert not (cfg.audio_store / "failed" / "n1.json").exists()
    assert failed_notes.parked(cfg.audio_store) == []
    notes = list((cfg.vault_path / "Geral" / "Anotações").glob("*.md"))
    assert len(notes) == 1 and "agora foi" in notes[0].read_text(encoding="utf-8")


def test_retry_parked_hands_the_recording_back(tmp_path):
    cfg = make_cfg(tmp_path)
    calls = {"n": 0}

    def flaky(wav, c):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TranscriptionError("out of VRAM on the small GPU")
        return Transcription("recuperada", 1.0, 1.3, "m.gguf")

    worker = NoteWorker(cfg, processor=None, transcribe_fn=flaky)
    worker.start()
    worker.submit(IncomingNote("n1", make_wav(cfg.audio_store / "n1.wav"), {}))
    worker.stop(timeout=10)
    assert len(failed_notes.parked(cfg.audio_store)) == 1

    # A fresh worker, as at the next start of the bridge.
    again = NoteWorker(cfg, processor=None, transcribe_fn=flaky)
    again.start()
    assert again.retry_parked() == 1
    again.stop(timeout=10)

    notes = list((cfg.vault_path / "Geral" / "Anotações").glob("*.md"))
    assert len(notes) == 1 and "recuperada" in notes[0].read_text(encoding="utf-8")
    assert failed_notes.parked(cfg.audio_store) == []
    assert not (cfg.audio_store / "failed" / "n1.json").exists()


def test_retry_parked_is_zero_when_nothing_failed(tmp_path):
    worker = NoteWorker(make_cfg(tmp_path), processor=None, transcribe_fn=None)
    assert worker.retry_parked() == 0
