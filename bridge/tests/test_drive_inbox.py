from datetime import datetime, timedelta, timezone

from handy_bridge.drive_inbox import RemoteFile, plan_inbox

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def at(minutes_ago: int) -> datetime:
    return NOW - timedelta(minutes=minutes_ago)


def test_a_pair_is_ready():
    files = [
        RemoteFile("w1", "20260910-120000.wav", at(1)),
        RemoteFile("s1", "20260910-120000.json", at(1)),
    ]
    ready = plan_inbox(files, processed_ids=set(), now=NOW)
    assert len(ready) == 1
    assert ready[0].note_id == "20260910-120000"
    assert ready[0].wav.id == "w1"
    assert ready[0].sidecar.id == "s1"


def test_a_wav_without_its_sidecar_waits_for_the_grace_period():
    files = [RemoteFile("w1", "20260910-120000.wav", at(1))]
    assert plan_inbox(files, processed_ids=set(), now=NOW) == []


def test_a_wav_whose_sidecar_never_came_is_ready_after_the_grace_period():
    # Sidecar perdido não custa a nota: ela sobe sem âncora.
    files = [RemoteFile("w1", "20260910-114000.wav", at(20))]
    ready = plan_inbox(files, processed_ids=set(), now=NOW)
    assert len(ready) == 1
    assert ready[0].sidecar is None


def test_an_already_processed_wav_is_not_offered_again():
    files = [
        RemoteFile("w1", "20260910-120000.wav", at(10)),
        RemoteFile("s1", "20260910-120000.json", at(10)),
    ]
    assert plan_inbox(files, processed_ids={"w1"}, now=NOW) == []


def test_a_sidecar_without_its_recording_is_ignored():
    files = [RemoteFile("s1", "20260910-120000.json", at(10))]
    assert plan_inbox(files, processed_ids=set(), now=NOW) == []


def test_the_oldest_recording_is_offered_first():
    files = [
        RemoteFile("w2", "20260910-120000.wav", at(1)),
        RemoteFile("s2", "20260910-120000.json", at(1)),
        RemoteFile("w1", "20260910-110000.wav", at(60)),
        RemoteFile("s1", "20260910-110000.json", at(60)),
    ]
    ready = plan_inbox(files, processed_ids=set(), now=NOW)
    assert [r.note_id for r in ready] == ["20260910-110000", "20260910-120000"]


def test_files_that_are_neither_wav_nor_sidecar_are_ignored():
    files = [RemoteFile("x", "leia-me.txt", at(10))]
    assert plan_inbox(files, processed_ids=set(), now=NOW) == []
