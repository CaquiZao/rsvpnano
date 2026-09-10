import json
import struct
from datetime import datetime, timedelta, timezone

from handy_bridge.drive_inbox import RemoteFile
from handy_bridge.drive_poller import DrivePoller, ProcessedIds
from handy_bridge.transcriber import Transcription
from handy_bridge.worker import NoteWorker

# Sem prefixo `tests.`: não há tests/__init__.py, e esta é a convenção do repo
# (test_chapters.py e test_pipeline.py importam de test_epub assim).
from test_drive_poller import FakeDrive, make_cfg

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def wav_bytes(seconds: int = 1) -> bytes:
    data = b"\x00\x01" * (16000 * seconds)
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16)
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def test_a_note_that_arrived_by_drive_lands_in_the_vault(tmp_path):
    cfg = make_cfg(tmp_path)
    # A transcrição é injetada como em tests/test_worker.py: sem Handy, sem áudio
    # real, sem tokens.
    worker = NoteWorker(
        cfg,
        processor=None,
        transcribe_fn=lambda wav, c: Transcription(
            "Pendencia pesquisar a caverna de Chauvet", 2.0, 1.3, "m.gguf"
        ),
    )
    worker.start()

    created = NOW - timedelta(minutes=1)
    files = [
        RemoteFile("w1", "20260910-120000.wav", created),
        RemoteFile("s1", "20260910-120000.json", created),
    ]
    drive = FakeDrive(
        files,
        {
            "w1": wav_bytes(2),
            "s1": json.dumps(
                {"clock_synced": True, "recorded_at": "2026-09-10T12:00:00"}
            ).encode("utf-8"),
        },
    )
    DrivePoller(
        cfg, drive, worker.submit, ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW
    ).poll_once()

    worker.stop(timeout=10)

    # Sem `book` no sidecar, a nota é solta e cai na mesma pasta que
    # test_worker.py verifica para a rota HTTP: Geral/Anotações.
    notes = list((cfg.vault_path / "Geral" / "Anotações").glob("*.md"))
    assert len(notes) == 1
    assert "chauvet" in notes[0].read_text(encoding="utf-8").lower()
