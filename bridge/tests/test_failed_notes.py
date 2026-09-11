import json
import struct
from pathlib import Path

from handy_bridge import failed_notes
from handy_bridge.pipeline import IncomingNote


def make_wav(path: Path) -> Path:
    data = bytes(32000)
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


def incoming(store: Path, note_id: str = "boot-0004-00033920") -> IncomingNote:
    return IncomingNote(note_id, make_wav(store / f"{note_id}.wav"), {"book": "Sapiens"})


def test_park_keeps_the_recording(tmp_path):
    store = tmp_path / "audio"
    note = incoming(store)
    original = note.wav_path.read_bytes()

    failed_notes.park(store, note, "Handy failed with exit code 3221225477")

    assert not note.wav_path.exists(), "the recording moved out of the live store"
    assert (store / "failed" / "boot-0004-00033920.wav").read_bytes() == original


def test_parked_reads_back_everything_a_retry_needs(tmp_path):
    store = tmp_path / "audio"
    failed_notes.park(store, incoming(store), "Vulkan died")

    waiting = failed_notes.parked(store)
    assert len(waiting) == 1
    assert waiting[0].note_id == "boot-0004-00033920"
    assert waiting[0].meta == {"book": "Sapiens"}
    assert waiting[0].attempts == 1
    assert "Vulkan" in waiting[0].reason


def test_a_second_failure_counts_up(tmp_path):
    store = tmp_path / "audio"
    failed_notes.park(store, incoming(store), "first")
    back = failed_notes.release(store, failed_notes.parked(store)[0])

    assert failed_notes.park(store, back, "second") == 2
    assert failed_notes.parked(store)[0].attempts == 2


def test_stops_offering_a_recording_that_keeps_failing(tmp_path):
    """Otherwise every restart re-runs the same doomed transcription forever."""
    store = tmp_path / "audio"
    note = incoming(store)
    for _ in range(failed_notes.MAX_ATTEMPTS - 1):
        failed_notes.park(store, note, "still broken")
        note = failed_notes.release(store, failed_notes.parked(store)[0])
    failed_notes.park(store, note, "still broken")

    assert failed_notes.parked(store) == []
    # Given up on, never deleted: the recording is the one thing nobody can remake.
    assert (store / "failed" / "boot-0004-00033920.wav").exists()


def test_release_puts_the_wav_where_the_pipeline_expects_it(tmp_path):
    store = tmp_path / "audio"
    failed_notes.park(store, incoming(store), "boom")

    back = failed_notes.release(store, failed_notes.parked(store)[0])

    assert back.wav_path == store / "boot-0004-00033920.wav"
    assert back.wav_path.exists()
    assert back.meta == {"book": "Sapiens"}
    # The record stays until the note actually lands, so a retry that fails
    # again still knows how many tries it has had.
    assert (store / "failed" / "boot-0004-00033920.json").exists()


def test_forget_drops_the_record(tmp_path):
    store = tmp_path / "audio"
    failed_notes.park(store, incoming(store), "boom")

    failed_notes.forget(store, "boot-0004-00033920")

    assert failed_notes.parked(store) == []
    assert not (store / "failed" / "boot-0004-00033920.json").exists()


def test_forget_is_quiet_when_there_is_nothing_to_forget(tmp_path):
    """Called after every success, so the common case is no record at all."""
    failed_notes.forget(tmp_path / "audio", "never-failed")


def test_a_record_without_its_recording_is_not_offered(tmp_path):
    store = tmp_path / "audio"
    failed_notes.park(store, incoming(store), "boom")
    (store / "failed" / "boot-0004-00033920.wav").unlink()

    assert failed_notes.parked(store) == []


def test_an_unreadable_record_is_skipped_not_fatal(tmp_path):
    store = tmp_path / "audio"
    failed_notes.park(store, incoming(store), "boom")
    good = failed_notes.parked(store)[0]
    (store / "failed" / "corrupt.json").write_text("{not json", encoding="utf-8")
    make_wav(store / "failed" / "corrupt.wav")

    assert [n.note_id for n in failed_notes.parked(store)] == [good.note_id]


def test_parked_is_empty_when_nothing_ever_failed(tmp_path):
    assert failed_notes.parked(tmp_path / "audio") == []


def test_the_record_says_when_it_failed(tmp_path):
    store = tmp_path / "audio"
    failed_notes.park(store, incoming(store), "boom")

    record = json.loads((store / "failed" / "boot-0004-00033920.json").read_text("utf-8"))
    assert record["failed_at"]
