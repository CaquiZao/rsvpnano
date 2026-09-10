import json
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from handy_bridge.config import AsrConfig, Config, DriveConfig, PostProcessConfig
from handy_bridge.drive_inbox import RemoteFile
from handy_bridge.drive_poller import DrivePoller, ProcessedIds

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def wav_bytes(seconds: float = 2) -> bytes:
    """Um WAV PCM que wav.inspect aceita, no formato que o device grava."""
    data = b"\x00\x01" * int(16000 * seconds)
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


WAV = wav_bytes(2)


def make_cfg(tmp_path) -> Config:
    vault = tmp_path / "Reading"
    vault.mkdir(parents=True, exist_ok=True)
    return Config(
        vault_path=vault,
        inbox_folder="Inbox",
        audio_store=tmp_path / "audio",
        port=8787,
        asr=AsrConfig(handy_exe=tmp_path / "handy.exe", model="m.gguf", timeout_s=900),
        post_process=PostProcessConfig(enabled=False, backend="none", model=""),
        drive=DriveConfig(enabled=True, client_id="c", client_secret="s",
                          refresh_token="r", folder_id="f"),
    )


class FakeDrive:
    def __init__(self, files, blobs):
        self.files = files
        self.blobs = blobs
        self.deleted: list[str] = []
        self.delete_fails = False

    def list_inbox(self):
        return list(self.files)

    def download(self, file_id):
        return self.blobs[file_id]

    def delete(self, file_id):
        if self.delete_fails:
            raise RuntimeError("delete falhou")
        self.deleted.append(file_id)


def pair(created_at=NOW - timedelta(minutes=1)):
    return [
        RemoteFile("w1", "20260910-120000.wav", created_at),
        RemoteFile("s1", "20260910-120000.json", created_at),
    ]


def blobs(meta=None):
    payload = {"clock_synced": True, "recorded_at": "2026-09-10T12:00:00"} if meta is None else meta
    return {"w1": WAV, "s1": json.dumps(payload).encode("utf-8")}


def test_hands_a_ready_note_to_the_worker(tmp_path):
    cfg = make_cfg(tmp_path)
    submitted = []
    drive = FakeDrive(pair(), blobs())
    poller = DrivePoller(cfg, drive, submitted.append, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)

    assert poller.poll_once() == 1
    assert len(submitted) == 1
    assert submitted[0].note_id == "20260910-120000"
    assert submitted[0].meta["clock_synced"] is True


def test_writes_the_audio_where_the_http_route_writes_it(tmp_path):
    cfg = make_cfg(tmp_path)
    drive = FakeDrive(pair(), blobs())
    poller = DrivePoller(cfg, drive, lambda n: None, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)
    poller.poll_once()
    assert (cfg.audio_store / "20260910-120000.wav").read_bytes() == WAV


def test_removes_both_files_from_the_drive_after_handing_over(tmp_path):
    cfg = make_cfg(tmp_path)
    drive = FakeDrive(pair(), blobs())
    poller = DrivePoller(cfg, drive, lambda n: None, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)
    poller.poll_once()
    assert sorted(drive.deleted) == ["s1", "w1"]


def test_a_failed_delete_does_not_produce_a_second_note(tmp_path):
    # O arquivo continua no Drive, mas o id já está marcado como processado.
    cfg = make_cfg(tmp_path)
    submitted = []
    drive = FakeDrive(pair(), blobs())
    drive.delete_fails = True
    state = ProcessedIds(tmp_path / "seen.json")
    poller = DrivePoller(cfg, drive, submitted.append, state, now=lambda: NOW)

    assert poller.poll_once() == 1
    assert poller.poll_once() == 0
    assert len(submitted) == 1


def test_a_note_already_processed_in_an_earlier_run_is_skipped(tmp_path):
    cfg = make_cfg(tmp_path)
    state_path = tmp_path / "seen.json"
    ProcessedIds(state_path).add("20260910-120000")

    submitted = []
    poller = DrivePoller(cfg, FakeDrive(pair(), blobs()), submitted.append,
                         ProcessedIds(state_path), now=lambda: NOW)
    assert poller.poll_once() == 0
    assert submitted == []


def test_the_state_file_records_the_note_id(tmp_path):
    # A chave do estado é o note_id (o stem), não o file id do Drive: um
    # retry do device sobe o mesmo arquivo com um id novo, e é o stem que
    # identifica a gravação já entregue.
    cfg = make_cfg(tmp_path)
    state_path = tmp_path / "seen.json"
    poller = DrivePoller(cfg, FakeDrive(pair(), blobs()), lambda n: None,
                         ProcessedIds(state_path), now=lambda: NOW)
    poller.poll_once()
    assert json.loads(state_path.read_text(encoding="utf-8")) == ["20260910-120000"]


def test_the_poller_deletes_extra_copies_along_with_the_wav_and_sidecar(tmp_path):
    cfg = make_cfg(tmp_path)
    files = [
        RemoteFile("w1", "20260910-120000.wav", NOW - timedelta(minutes=10)),
        RemoteFile("w2", "20260910-120000.wav", NOW - timedelta(minutes=1)),
        RemoteFile("s1", "20260910-120000.json", NOW - timedelta(minutes=1)),
    ]
    blob = {
        "w1": WAV,
        "w2": wav_bytes(3),
        "s1": json.dumps({"clock_synced": True}).encode("utf-8"),
    }
    drive = FakeDrive(files, blob)
    poller = DrivePoller(cfg, drive, lambda n: None, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)

    assert poller.poll_once() == 1
    assert sorted(drive.deleted) == ["s1", "w1", "w2"]


def test_an_unparseable_sidecar_is_refused_the_way_the_lan_route_refuses_it(tmp_path):
    # POST /v1/notes responde 400 a meta que não é JSON, guardando o áudio. O
    # poller escrevia a nota sem âncora -- a mesma gravação virava nota por uma
    # porta e não pela outra. O sidecar é escrito pelo firmware, então seus
    # bytes exatos são o relatório do bug e são guardados junto.
    cfg = make_cfg(tmp_path)
    submitted = []
    drive = FakeDrive(pair(), {"w1": WAV, "s1": b"{nao e json"})
    state = ProcessedIds(tmp_path / "seen.json")
    poller = DrivePoller(cfg, drive, submitted.append, state, now=lambda: NOW)

    assert poller.poll_once() == 0
    assert submitted == []
    kept = cfg.audio_store / "rejected"
    assert (kept / "20260910-120000.wav").read_bytes() == WAV
    assert (kept / "20260910-120000.meta.txt").read_text(encoding="utf-8") == "{nao e json"
    # Recusada uma vez, não a cada 30 segundos para sempre.
    assert "20260910-120000" in state.snapshot()
    assert sorted(drive.deleted) == ["s1", "w1"]


def test_a_missing_sidecar_is_still_tolerated(tmp_path):
    # A distinção que não pode ser achatada: sidecar *ausente* a spec autoriza
    # (a nota entra sem âncora), sidecar *ilegível* é recusado.
    cfg = make_cfg(tmp_path)
    submitted = []
    lone = [RemoteFile("w1", "20260910-114000.wav", NOW - timedelta(minutes=20))]
    poller = DrivePoller(cfg, FakeDrive(lone, {"w1": WAV}), submitted.append,
                         ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW)

    assert poller.poll_once() == 1
    assert submitted[0].meta == {}


def test_an_unusable_wav_is_refused_instead_of_exploding_inside_the_worker(tmp_path):
    # pipeline.py chama wav.inspect sem guarda: um WAV inutilizável levantava
    # ali dentro, era engolido numa linha de log pelo worker, e a cópia no
    # Drive já tinha sido apagada. A rota da LAN recusa isso com 400 e guarda
    # o áudio; aqui não havia nem uma coisa nem a outra.
    cfg = make_cfg(tmp_path)
    submitted = []
    drive = FakeDrive(pair(), {"w1": b"isto nao e um wav", "s1": blobs()["s1"]})
    state = ProcessedIds(tmp_path / "seen.json")
    poller = DrivePoller(cfg, drive, submitted.append, state, now=lambda: NOW)

    assert poller.poll_once() == 0
    assert submitted == []
    assert (cfg.audio_store / "rejected" / "20260910-120000.wav").exists()
    assert "20260910-120000" in state.snapshot()
    assert sorted(drive.deleted) == ["s1", "w1"]


def test_a_sub_second_recording_is_refused_instead_of_becoming_an_empty_note(tmp_path):
    cfg = make_cfg(tmp_path)
    submitted = []
    drive = FakeDrive(pair(), {"w1": wav_bytes(0.5), "s1": blobs()["s1"]})
    poller = DrivePoller(cfg, drive, submitted.append, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)

    assert poller.poll_once() == 0
    assert submitted == []
    assert (cfg.audio_store / "rejected" / "20260910-120000.wav").exists()


def test_stop_waits_for_the_thread_to_actually_stop(tmp_path):
    cfg = make_cfg(tmp_path)
    poller = DrivePoller(cfg, FakeDrive([], {}), lambda n: None,
                         ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW)
    poller.start()
    poller.stop()
    assert not poller._thread.is_alive()


def test_nothing_ready_means_nothing_submitted(tmp_path):
    cfg = make_cfg(tmp_path)
    submitted = []
    lone_wav = [RemoteFile("w1", "20260910-120000.wav", NOW)]
    poller = DrivePoller(cfg, FakeDrive(lone_wav, {"w1": b"x"}), submitted.append,
                         ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW)
    assert poller.poll_once() == 0
    assert submitted == []


def test_the_poller_drains_what_it_will_never_process_again(tmp_path):
    # O delete é melhor-esforço, então sobra lixo: um par já processado que
    # não foi removido, e um sidecar órfão que plan_inbox nunca vai casar. Com
    # ~200 desses a listagem satura e gravação nova nenhuma volta a aparecer.
    cfg = make_cfg(tmp_path)
    files = [
        RemoteFile("w1", "20260910-120000.wav", NOW - timedelta(minutes=10)),
        RemoteFile("s1", "20260910-120000.json", NOW - timedelta(minutes=10)),
        RemoteFile("s9", "20260910-110000.json", NOW - timedelta(minutes=60)),
    ]
    drive = FakeDrive(files, blobs())
    state = ProcessedIds(tmp_path / "seen.json")
    state.add("20260910-120000")
    submitted = []
    poller = DrivePoller(cfg, drive, submitted.append, state, now=lambda: NOW)

    assert poller.poll_once() == 0
    assert submitted == []
    assert sorted(drive.deleted) == ["s1", "s9", "w1"]


def test_a_hostile_drive_file_name_stays_inside_the_audio_store(tmp_path):
    cfg = make_cfg(tmp_path)
    files = [
        RemoteFile("w1", "../../../evil.wav", NOW - timedelta(minutes=10)),
        RemoteFile("s1", "../../../evil.json", NOW - timedelta(minutes=10)),
    ]
    drive = FakeDrive(files, {"w1": WAV, "s1": json.dumps({"clock_synced": True}).encode("utf-8")})
    poller = DrivePoller(cfg, drive, lambda n: None, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)
    poller.poll_once()
    assert (cfg.audio_store / "evil.wav").exists()
    assert not (tmp_path.parent / "evil.wav").exists()


def test_many_threads_recording_at_once_all_survive_in_the_file(tmp_path):
    # Dois fios escrevem neste store: a thread do poller e o event loop do
    # uvicorn, com a mesma instância (__main__.py passa uma só). Sem lock e com
    # um `.tmp` fixo os dois escreviam o mesmo arquivo temporário e o perdedor
    # do replace levantava FileNotFoundError (PermissionError no Windows) --
    # depois de a nota já ter sido entregue -- ou deixava um JSON curto por
    # cima do prefixo de um mais longo, que no boot seguinte não é lido e
    # reprocessa a pasta inteira.
    state = ProcessedIds(tmp_path / "seen.json")
    ids = [f"boot-{i:08d}" for i in range(200)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(state.add, ids))

    assert state.snapshot() == set(ids)
    assert json.loads((tmp_path / "seen.json").read_text(encoding="utf-8")) == sorted(ids)
    # Nenhum temporario sobrando na pasta de estado.
    assert list(tmp_path.glob("*.tmp")) == []


class RecusaGravar:
    """Um store cuja escrita falha: disco cheio, permissao, corrida."""

    def snapshot(self):
        return set()

    def add(self, note_id):
        raise OSError("disco cheio")


def test_a_state_file_that_cannot_be_written_does_not_undo_a_refusal(tmp_path):
    # No _refuse a gravação já está segura em rejected/: registrar que ela foi
    # processada é contabilidade, e uma falha aí não pode abortar o poll nem
    # deixar o arquivo no Drive para ser recusado a cada 30 segundos.
    cfg = make_cfg(tmp_path)
    drive = FakeDrive(pair(), {"w1": WAV, "s1": b"{nao e json"})
    poller = DrivePoller(cfg, drive, lambda n: None, RecusaGravar(), now=lambda: NOW)

    assert poller.poll_once() == 0
    assert (cfg.audio_store / "rejected" / "20260910-120000.wav").read_bytes() == WAV
    assert sorted(drive.deleted) == ["s1", "w1"]
