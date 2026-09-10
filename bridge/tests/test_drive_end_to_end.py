import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from handy_bridge.drive_inbox import RemoteFile
from handy_bridge.drive_poller import DrivePoller, ProcessedIds
from handy_bridge.server import create_app
from handy_bridge.transcriber import Transcription
from handy_bridge.worker import NoteWorker

# Sem prefixo `tests.`: não há tests/__init__.py, e esta é a convenção do repo
# (test_chapters.py e test_pipeline.py importam de test_epub assim).
from test_drive_poller import FakeDrive, make_cfg, wav_bytes

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


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


META = json.dumps({"clock_synced": True, "recorded_at": "2026-09-10T12:00:00"})


def spoken(text: str = "Uma nota de voz qualquer"):
    return lambda wav, cfg: Transcription(text, 2.0, 1.3, "m.gguf")


def notes_in(cfg):
    return list((cfg.vault_path / "Geral" / "Anotações").glob("*.md"))


def test_a_recording_delivered_by_lan_does_not_come_back_as_a_second_note(tmp_path):
    # O caso Drive->LAN que a spec não considerou: o upload do Drive confirma o
    # WAV e falha no sidecar, então a entrada fica na fila; o flush seguinte
    # acha o bridge na rede e entrega por POST /v1/notes (nota #1). A cópia
    # que ficou no Drive passa do prazo de carência e o poller escreveria a
    # nota #2 -- e _unique_path faz dela um arquivo novo, não uma sobrescrita.
    cfg = make_cfg(tmp_path)
    cfg.audio_store.mkdir(parents=True, exist_ok=True)
    worker = NoteWorker(cfg, processor=None, transcribe_fn=spoken())
    worker.start()

    # Um único armazenamento de note_ids para as duas portas de entrada.
    seen = ProcessedIds(tmp_path / "seen.json")
    client = TestClient(create_app(cfg, submit=worker.submit, processed=seen))
    wav = wav_bytes(2)
    response = client.post(
        "/v1/notes",
        files={"audio": ("20260910-120000.wav", wav, "audio/wav")},
        data={"meta": META},
    )
    assert response.status_code == 200

    stale = NOW - timedelta(minutes=10)
    drive = FakeDrive(
        [
            RemoteFile("w1", "20260910-120000.wav", stale),
            RemoteFile("s1", "20260910-120000.json", stale),
        ],
        {"w1": wav, "s1": META.encode("utf-8")},
    )
    poller = DrivePoller(cfg, drive, worker.submit, seen, now=lambda: NOW)
    assert poller.poll_once() == 0

    worker.stop(timeout=10)
    assert len(notes_in(cfg)) == 1
    # E a cópia no Drive não fica lá para sempre entupindo a listagem.
    assert sorted(drive.deleted) == ["s1", "w1"]
