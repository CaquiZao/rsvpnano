import json
import logging
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
        self.downloaded: list[str] = []
        self.deleted: list[str] = []
        self.delete_fails = False

    def list_inbox(self):
        return list(self.files)

    def download(self, file_id):
        self.downloaded.append(file_id)
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


def test_a_second_wav_under_the_same_stem_is_neither_downloaded_nor_deleted(tmp_path):
    # Dois `.wav` com o mesmo stem eram tratados como retry da mesma gravação:
    # o mais novo era apagado sem nunca ser baixado. Mas o stem é `boot-%08lu`
    # sem relógio sincronizado, então boot 1 (wav subiu, sidecar falhou) e
    # boot 2 (wav+json no mesmo ms-desde-boot) põem DUAS gravações diferentes
    # sob um nome. A nota sai da mais antiga e a outra, que ninguém ouviu,
    # era destruída sem cópia em lugar nenhum. Ela fica na pasta -- e o aviso
    # de stem já processado a reporta no poll seguinte.
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
    # A nota entregue sai da pasta; a cópia extra não, e nem os bytes dela
    # foram pedidos ao Drive.
    assert sorted(drive.deleted) == ["s1", "w1"]
    assert sorted(drive.downloaded) == ["s1", "w1"]


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


def test_a_processed_wav_is_neither_downloaded_nor_deleted(tmp_path):
    # A colisão real: boot-00042318 foi entregue pela LAN e ficou marcado como
    # processado; um boot seguinte cunha o mesmo stem para OUTRA gravação, que
    # cai para o Drive. Drenar isso -- apagando, ou baixando para depois apagar
    # -- destruiu gravação e custou o metadado dela. O arquivo fica na pasta,
    # intocado: invisível ao poller e recuperável à mão. O que impede uma pasta
    # cheia de esconder uma gravação nova é a paginação de list_inbox.
    cfg = make_cfg(tmp_path)
    files = [RemoteFile("w1", "boot-00042318.wav", NOW - timedelta(minutes=10))]
    drive = FakeDrive(files, {"w1": wav_bytes(3)})
    state = ProcessedIds(tmp_path / "seen.json")
    state.add("boot-00042318")
    submitted = []
    poller = DrivePoller(cfg, drive, submitted.append, state, now=lambda: NOW)

    assert poller.poll_once() == 0
    assert submitted == []
    assert drive.downloaded == []
    assert drive.deleted == []
    # E nada foi escrito em disco: nem nota, nem cópia em rejected/.
    assert list(cfg.audio_store.glob("**/*")) == []


def test_a_processed_stem_still_in_the_folder_is_named_in_the_log(tmp_path, caplog):
    # Deixar o arquivo na pasta só é "recuperável à mão" se alguém souber que
    # ele está lá. O device já apagou a cópia dele (DriveResult::Sent ->
    # QueueAction::Delete), então este `.wav` pode ser a última cópia da
    # gravação em qualquer lugar -- e sem linha de log os documentos ensinavam
    # a esvaziar a pasta, que é destruí-la.
    cfg = make_cfg(tmp_path)
    files = [
        RemoteFile("w1", "boot-00042318.wav", NOW - timedelta(minutes=10)),
        RemoteFile("s1", "boot-00042318.json", NOW - timedelta(minutes=10)),
    ]
    drive = FakeDrive(files, {"w1": wav_bytes(3), "s1": b"{}"})
    state = ProcessedIds(tmp_path / "seen.json")
    state.add("boot-00042318")
    submitted = []
    poller = DrivePoller(cfg, drive, submitted.append, state, now=lambda: NOW)

    with caplog.at_level(logging.WARNING):
        assert poller.poll_once() == 0

    assert submitted == []
    assert drive.deleted == []
    assert drive.downloaded == []
    avisos = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(avisos) == 1
    assert "boot-00042318" in avisos[0]


def test_the_extra_copy_left_in_the_folder_is_named_in_the_log_on_the_next_poll(tmp_path, caplog):
    # O `.wav` extra do mesmo stem não é mais apagado (ver
    # test_a_second_wav_under_the_same_stem...). Quem o torna visível é este
    # aviso: entregue a nota, o stem está marcado como processado, e no poll
    # seguinte o que sobrou na pasta é exatamente um arquivo de stem
    # processado. Sem isso a remoção sai e o silêncio fica.
    cfg = make_cfg(tmp_path)
    files = [
        RemoteFile("w1", "boot-00042318.wav", NOW - timedelta(minutes=10)),
        RemoteFile("w2", "boot-00042318.wav", NOW - timedelta(minutes=1)),
        RemoteFile("s1", "boot-00042318.json", NOW - timedelta(minutes=1)),
    ]
    blob = {"w1": WAV, "w2": wav_bytes(3), "s1": json.dumps({"clock_synced": True}).encode("utf-8")}
    drive = FakeDrive(files, blob)
    poller = DrivePoller(cfg, drive, lambda n: None, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)
    assert poller.poll_once() == 1

    # O que o Drive lista no poll seguinte: a nota entregue saiu, a cópia
    # extra ficou.
    drive.files = [f for f in drive.files if f.id not in drive.deleted]
    assert [f.id for f in drive.files] == ["w2"]

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert poller.poll_once() == 0

    avisos = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(avisos) == 1
    assert "boot-00042318" in avisos[0]
    assert drive.deleted == ["w1", "s1"]


def test_the_sidecar_of_a_processed_note_stays_with_its_wav(tmp_path):
    # Se uma gravação colidida vai ficar na pasta esperando resgate à mão, o
    # metadado dela tem que ficar junto: senão quem abre a pasta acha um
    # boot-00042318.wav e nada que diga de quando ele é.
    cfg = make_cfg(tmp_path)
    files = [
        RemoteFile("w1", "boot-00042318.wav", NOW - timedelta(minutes=10)),
        RemoteFile("s1", "boot-00042318.json", NOW - timedelta(minutes=10)),
    ]
    drive = FakeDrive(files, {"w1": wav_bytes(3), "s1": b"{}"})
    state = ProcessedIds(tmp_path / "seen.json")
    state.add("boot-00042318")
    poller = DrivePoller(cfg, drive, lambda n: None, state, now=lambda: NOW)

    assert poller.poll_once() == 0
    assert drive.downloaded == []
    assert drive.deleted == []


def test_an_orphan_sidecar_is_dropped_without_downloading_anything(tmp_path):
    # Um .json sem .wav nenhum na pasta não carrega gravação: passado o prazo
    # ninguém vai casar com ele, então sai -- e baixá-lo seria rede gasta à
    # toa. É a única remoção que não vem de uma nota entregue ou recusada.
    cfg = make_cfg(tmp_path)
    files = [RemoteFile("s9", "boot-00042318.json", NOW - timedelta(minutes=60))]
    drive = FakeDrive(files, {"s9": b"{}"})
    poller = DrivePoller(cfg, drive, lambda n: None, ProcessedIds(tmp_path / "seen.json"),
                         now=lambda: NOW)

    assert poller.poll_once() == 0
    assert drive.deleted == ["s9"]
    assert drive.downloaded == []
    assert list((cfg.audio_store / "rejected").glob("*")) == []


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
