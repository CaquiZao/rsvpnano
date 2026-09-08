import struct
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from handy_bridge.config import AsrConfig, Config, PostProcessConfig
from handy_bridge.pipeline import IncomingNote, process_note, resolve_recorded_at
from handy_bridge.postprocess import PostProcessError, PostProcessResult
from handy_bridge.transcriber import Transcription, TranscriptionError

ARRIVED = datetime(2026, 9, 7, 15, 0, 0)


def make_wav(path: Path, seconds: int = 1) -> Path:
    frames = 16000 * seconds
    data = b"\x00\x01" * frames
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


def make_cfg(tmp_path, *, pp_enabled=True) -> Config:
    vault = tmp_path / "Reading"
    vault.mkdir(exist_ok=True)
    return Config(
        vault_path=vault,
        inbox_folder="Inbox",
        audio_store=tmp_path / "audio",
        port=8787,
        asr=AsrConfig(handy_exe=tmp_path / "handy.exe", model="m.gguf", timeout_s=900),
        post_process=PostProcessConfig(
            enabled=pp_enabled, backend="claude_cli", model="haiku"
        ),
    )


class FakeProcessor:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def process(self, transcript):
        if self.error:
            raise self.error
        return self.result


def ok_transcribe(text="ola mundo"):
    return lambda wav, cfg: Transcription(
        text=text, audio_secs=1.0, rtf=1.3, model="m.gguf"
    )


# --- clock reconstruction -------------------------------------------------


def test_uses_device_clock_when_synced():
    meta = {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == datetime(2026, 9, 7, 14, 32, 11)
    assert estimated is False


def test_reconstructs_time_when_clock_not_synced():
    # gravado 5 minutos de uptime antes do upload
    meta = {"clock_synced": False, "uptime_ms": 60_000, "uptime_at_upload_ms": 360_000}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == ARRIVED - timedelta(milliseconds=300_000)
    assert estimated is True


def test_falls_back_to_arrival_when_uptime_missing():
    when, estimated = resolve_recorded_at({"clock_synced": False}, ARRIVED)
    assert when == ARRIVED
    assert estimated is True


def test_falls_back_to_arrival_when_recorded_at_unparseable():
    meta = {"clock_synced": True, "recorded_at": "not-a-date"}
    when, estimated = resolve_recorded_at(meta, ARRIVED)
    assert when == ARRIVED
    assert estimated is True


# --- orchestration --------------------------------------------------------


def test_writes_note_with_post_processed_fields(tmp_path):
    cfg = make_cfg(tmp_path)
    wav = make_wav(tmp_path / "n.wav")
    incoming = IncomingNote(
        "n", wav, {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}
    )
    processor = FakeProcessor(
        PostProcessResult("Meu titulo", ["ideia"], "Texto limpo.")
    )

    path = process_note(
        incoming, cfg, transcribe_fn=ok_transcribe(), processor=processor
    )
    text = path.read_text(encoding="utf-8")

    assert path.name == "2026-09-07 1432 - Meu titulo.md"
    assert "Texto limpo." in text
    assert "> ola mundo" in text  # transcrição crua preservada
    assert "tags: [ideia]" in text


def test_degrades_to_raw_transcript_when_post_processing_fails(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    processor = FakeProcessor(error=PostProcessError("boom"))

    path = process_note(
        incoming, cfg, transcribe_fn=ok_transcribe(), processor=processor
    )
    text = path.read_text(encoding="utf-8")

    assert path.exists()
    assert "ola mundo" in text
    assert "2026-09-07 1432" in path.name


def test_works_with_post_processing_disabled(tmp_path):
    cfg = make_cfg(tmp_path, pp_enabled=False)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)
    assert "ola mundo" in path.read_text(encoding="utf-8")


def test_empty_transcription_still_writes_a_note(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(""), processor=None)
    assert path.exists()
    assert "(transcrição vazia)" in path.read_text(encoding="utf-8")


def test_repairs_truncated_wav_before_transcribing(tmp_path):
    cfg = make_cfg(tmp_path)
    wav = make_wav(tmp_path / "n.wav")
    # zera os tamanhos, simulando queda de energia durante a gravação
    with wav.open("r+b") as fh:
        fh.seek(4)
        fh.write(struct.pack("<I", 0))
        fh.seek(40)
        fh.write(struct.pack("<I", 0))

    incoming = IncomingNote(
        "n", wav, {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"}
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)

    assert path.exists()
    assert struct.unpack_from("<I", wav.read_bytes(), 40)[0] == 32000


def test_carries_book_anchor_from_meta_into_the_note(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {
            "clock_synced": True,
            "recorded_at": "2026-09-07T14:32:11",
            "book": "epdf.pub_sapiens",
            "word_offset": 12438,
            "excerpt": "a Revolução Agrícola foi a maior fraude da história",
        },
    )
    path = process_note(incoming, cfg, transcribe_fn=ok_transcribe(), processor=None)
    text = path.read_text(encoding="utf-8")

    assert 'book: "[[epdf.pub_sapiens]]"' in text
    assert "word_offset: 12438" in text
    assert "> [!quote] Trecho que eu estava lendo" in text


def test_standalone_note_has_no_anchor(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote(
        "n",
        make_wav(tmp_path / "n.wav"),
        {"clock_synced": True, "recorded_at": "2026-09-07T14:32:11"},
    )
    text = process_note(
        incoming, cfg, transcribe_fn=ok_transcribe(), processor=None
    ).read_text(encoding="utf-8")
    assert "book:" not in text
    assert "[!quote]" not in text


def test_transcription_failure_propagates(tmp_path):
    cfg = make_cfg(tmp_path)
    incoming = IncomingNote("n", make_wav(tmp_path / "n.wav"), {"clock_synced": True})

    def boom(wav, asr_cfg):
        raise TranscriptionError("handy exploded")

    with pytest.raises(TranscriptionError):
        process_note(incoming, cfg, transcribe_fn=boom, processor=None)
