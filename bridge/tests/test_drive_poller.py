import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from handy_bridge.config import AsrConfig, Config, DriveConfig, PostProcessConfig
from handy_bridge.drive_inbox import RemoteFile
from handy_bridge.drive_poller import DrivePoller, ProcessedIds

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


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
    return {"w1": b"RIFFxxxx", "s1": json.dumps(payload).encode("utf-8")}


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
    assert (cfg.audio_store / "20260910-120000.wav").read_bytes() == b"RIFFxxxx"


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
    ProcessedIds(state_path).add("w1")

    submitted = []
    poller = DrivePoller(cfg, FakeDrive(pair(), blobs()), submitted.append,
                         ProcessedIds(state_path), now=lambda: NOW)
    assert poller.poll_once() == 0
    assert submitted == []


def test_an_unreadable_sidecar_does_not_cost_the_note(tmp_path):
    cfg = make_cfg(tmp_path)
    submitted = []
    bad = {"w1": b"RIFFxxxx", "s1": b"{nao e json"}
    poller = DrivePoller(cfg, FakeDrive(pair(), bad), submitted.append,
                         ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW)

    assert poller.poll_once() == 1
    assert submitted[0].meta == {}


def test_nothing_ready_means_nothing_submitted(tmp_path):
    cfg = make_cfg(tmp_path)
    submitted = []
    lone_wav = [RemoteFile("w1", "20260910-120000.wav", NOW)]
    poller = DrivePoller(cfg, FakeDrive(lone_wav, {"w1": b"x"}), submitted.append,
                         ProcessedIds(tmp_path / "seen.json"), now=lambda: NOW)
    assert poller.poll_once() == 0
    assert submitted == []
