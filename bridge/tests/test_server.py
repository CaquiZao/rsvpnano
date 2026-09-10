import json
import logging
import struct

from fastapi.testclient import TestClient

from handy_bridge.config import AsrConfig, Config, PostProcessConfig
from handy_bridge.server import create_app


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


def client(tmp_path, submitted=None):
    cfg = make_cfg(tmp_path)
    cfg.audio_store.mkdir(parents=True, exist_ok=True)
    submit = submitted.append if submitted is not None else (lambda incoming: None)
    return TestClient(create_app(cfg, submit=submit)), cfg


def post_note(c, *, audio=None, meta=None):
    meta = (
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}
        if meta is None
        else meta
    )
    return c.post(
        "/v1/notes",
        files={"audio": ("20260907-143211.wav", audio or wav_bytes(), "audio/wav")},
        data={"meta": json.dumps(meta)},
    )


def test_accepts_note_and_returns_id(tmp_path):
    c, _ = client(tmp_path)
    resp = post_note(c)
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"
    assert resp.json()["id"] == "20260907-143211"


def test_persists_wav_to_audio_store(tmp_path):
    c, cfg = client(tmp_path)
    post_note(c)
    stored = list(cfg.audio_store.glob("*.wav"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == wav_bytes()


def test_hands_incoming_note_to_the_worker(tmp_path):
    submitted = []
    c, _ = client(tmp_path, submitted)
    post_note(c)
    assert len(submitted) == 1
    assert submitted[0].note_id == "20260907-143211"
    assert submitted[0].meta["clock_synced"] is True


def test_rejects_malformed_meta_json(tmp_path):
    c, _ = client(tmp_path)
    resp = c.post(
        "/v1/notes",
        files={"audio": ("a.wav", wav_bytes(), "audio/wav")},
        data={"meta": "{not json"},
    )
    assert resp.status_code == 400
    assert "meta" in resp.json()["error"]


def test_rejects_audio_too_short_to_be_speech(tmp_path):
    c, _ = client(tmp_path)
    tiny = wav_bytes()[:44] + b"\x00" * 100  # bem menos de 1 s
    resp = post_note(c, audio=tiny)
    assert resp.status_code == 400
    assert "short" in resp.json()["error"]


def test_rejects_non_wav_payload(tmp_path):
    c, _ = client(tmp_path)
    resp = post_note(c, audio=b"NOPE" + b"\x00" * 200)
    assert resp.status_code == 400


def test_health_endpoint(tmp_path):
    c, _ = client(tmp_path)
    assert c.get("/v1/health").status_code == 200
    assert c.get("/v1/health").json()["status"] == "ok"


def test_logs_why_a_note_was_refused(tmp_path, caplog):
    c, _ = client(tmp_path)
    with caplog.at_level(logging.WARNING, logger="handy_bridge.server"):
        post_note(c, audio=wav_bytes()[:44] + bytes(100))
    assert "20260907-143211" in caplog.text
    assert "short" in caplog.text


def test_keeps_refused_audio_for_diagnosis(tmp_path):
    c, cfg = client(tmp_path)
    tiny = wav_bytes()[:44] + bytes(100)
    post_note(c, audio=tiny)
    assert list(cfg.audio_store.glob("*.wav")) == []
    kept = list((cfg.audio_store / "rejected").glob("*.wav"))
    assert len(kept) == 1
    assert kept[0].read_bytes() == tiny


def test_keeps_audio_when_meta_is_unreadable(tmp_path):
    c, cfg = client(tmp_path)
    c.post(
        "/v1/notes",
        files={"audio": ("20260907-143211.wav", wav_bytes(), "audio/wav")},
        data={"meta": "{not json"},
    )
    kept = list((cfg.audio_store / "rejected").glob("*.wav"))
    assert len(kept) == 1


def test_keeps_unreadable_meta_next_to_its_audio(tmp_path):
    c, cfg = client(tmp_path)
    c.post(
        "/v1/notes",
        files={"audio": ("20260907-143211.wav", wav_bytes(), "audio/wav")},
        data={"meta": '{"clock_synced":tru'},
    )
    kept = cfg.audio_store / "rejected" / "20260907-143211.meta.txt"
    assert kept.read_text(encoding="utf-8") == '{"clock_synced":tru'


def test_logs_a_body_the_parser_cannot_read(tmp_path, caplog):
    c, _ = client(tmp_path)
    with caplog.at_level(logging.WARNING, logger="handy_bridge.server"):
        resp = c.post(
            "/v1/notes",
            content=b"not a multipart body at all",
            headers={"Content-Type": "multipart/form-data; boundary=rsvpnanoVoiceNoteBoundary"},
        )
    assert resp.status_code == 400
    assert "multipart/form-data" in caplog.text
    assert "27" in caplog.text  # o tamanho do corpo que chegou


def test_logs_a_request_missing_its_audio(tmp_path, caplog):
    c, _ = client(tmp_path)
    with caplog.at_level(logging.WARNING, logger="handy_bridge.server"):
        resp = c.post("/v1/notes", data={"meta": "{}"})
    assert resp.status_code == 422
    assert "audio" in caplog.text
