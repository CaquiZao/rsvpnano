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


def note_from_lan(tmp_path, wav, meta):
    """A nota que POST /v1/notes produz, no vault dele."""
    cfg = make_cfg(tmp_path / "lan")
    cfg.audio_store.mkdir(parents=True, exist_ok=True)
    worker = NoteWorker(cfg, processor=None, transcribe_fn=spoken())
    worker.start()
    client = TestClient(create_app(cfg, submit=worker.submit))
    response = client.post(
        "/v1/notes",
        files={"audio": ("20260910-120000.wav", wav, "audio/wav")},
        data={"meta": meta},
    )
    worker.stop(timeout=10)
    return cfg, response


def note_from_drive(tmp_path, wav, meta):
    """A nota que a porta do Drive produz, no vault dela."""
    cfg = make_cfg(tmp_path / "drive")
    worker = NoteWorker(cfg, processor=None, transcribe_fn=spoken())
    worker.start()
    created = NOW - timedelta(minutes=1)
    drive = FakeDrive(
        [
            RemoteFile("w1", "20260910-120000.wav", created),
            RemoteFile("s1", "20260910-120000.json", created),
        ],
        {"w1": wav, "s1": meta.encode("utf-8")},
    )
    handed = DrivePoller(
        cfg, drive, worker.submit, ProcessedIds(cfg.audio_store / "seen.json"), now=lambda: NOW
    ).poll_once()
    worker.stop(timeout=10)
    return cfg, handed


def test_the_two_entrances_produce_the_same_note(tmp_path):
    # O teste que a §10 da spec pediu e que não veio: não "uma nota chegou",
    # e sim que a nota vinda do Drive é a que o POST /v1/notes produziria.
    # É este teste que teria pegado a divergência de validação entre as duas
    # portas.
    wav = wav_bytes(2)
    meta = json.dumps(
        {
            "clock_synced": True,
            "recorded_at": "2026-09-10T12:00:00",
            "excerpt": "um trecho qualquer",
        }
    )

    lan_cfg, response = note_from_lan(tmp_path, wav, meta)
    drive_cfg, handed = note_from_drive(tmp_path, wav, meta)
    assert response.status_code == 200
    assert handed == 1

    lan_notes = notes_in(lan_cfg)
    drive_notes = notes_in(drive_cfg)
    assert len(lan_notes) == 1
    assert len(drive_notes) == 1
    # Mesma pasta no vault, mesmo nome de arquivo, mesmo conteúdo -- corpo,
    # kind e frontmatter inteiros.
    assert drive_notes[0].relative_to(drive_cfg.vault_path) == lan_notes[0].relative_to(
        lan_cfg.vault_path
    )
    assert drive_notes[0].read_text(encoding="utf-8") == lan_notes[0].read_text(
        encoding="utf-8"
    )


def test_what_one_entrance_refuses_the_other_refuses_too(tmp_path):
    # A mesma equivalência do lado da recusa: um WAV inutilizável não pode
    # virar nota por uma porta e 400 pela outra.
    meta = json.dumps({"clock_synced": True, "recorded_at": "2026-09-10T12:00:00"})
    broken = b"isto nao e um wav"

    lan_cfg, response = note_from_lan(tmp_path, broken, meta)
    drive_cfg, handed = note_from_drive(tmp_path, broken, meta)

    assert response.status_code == 400
    assert handed == 0
    assert notes_in(lan_cfg) == []
    assert notes_in(drive_cfg) == []
    # E o áudio das duas está no mesmo lugar, com o mesmo nome.
    assert (lan_cfg.audio_store / "rejected" / "20260910-120000.wav").exists()
    assert (drive_cfg.audio_store / "rejected" / "20260910-120000.wav").exists()
