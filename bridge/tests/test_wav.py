import struct
from pathlib import Path

import pytest

from handy_bridge.wav import InvalidWav, inspect, repair_header

SR, CH, BITS = 16000, 1, 16


def make_wav(path: Path, frames: int, *, zero_sizes: bool = False) -> Path:
    data = b"\x00\x01" * frames
    data_len = 0 if zero_sizes else len(data)
    riff_len = 0 if zero_sizes else 36 + len(data)
    header = (
        b"RIFF"
        + struct.pack("<I", riff_len)
        + b"WAVE"
        + b"fmt "
        + struct.pack(
            "<IHHIIHH", 16, 1, CH, SR, SR * CH * BITS // 8, CH * BITS // 8, BITS
        )
        + b"data"
        + struct.pack("<I", data_len)
    )
    path.write_bytes(header + data)
    return path


def test_inspect_reads_format(tmp_path):
    info = inspect(make_wav(tmp_path / "a.wav", SR))  # 1 second
    assert info.sample_rate == SR
    assert info.channels == CH
    assert info.bits_per_sample == BITS
    assert info.data_bytes == SR * 2
    assert info.duration_s == pytest.approx(1.0)


def test_repair_fixes_zeroed_sizes(tmp_path):
    p = make_wav(tmp_path / "b.wav", SR, zero_sizes=True)
    assert repair_header(p) is True
    info = inspect(p)
    assert info.data_bytes == SR * 2
    assert info.duration_s == pytest.approx(1.0)


def test_repair_is_noop_on_healthy_file(tmp_path):
    p = make_wav(tmp_path / "c.wav", SR)
    before = p.read_bytes()
    assert repair_header(p) is False
    assert p.read_bytes() == before


def test_rejects_non_riff(tmp_path):
    p = tmp_path / "d.wav"
    p.write_bytes(b"NOPE" + b"\x00" * 100)
    with pytest.raises(InvalidWav, match="RIFF"):
        inspect(p)


def test_rejects_file_shorter_than_header(tmp_path):
    p = tmp_path / "e.wav"
    p.write_bytes(b"RIFF")
    with pytest.raises(InvalidWav):
        inspect(p)
