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


def make_wav_ext(
    path: Path, frames: int, *, fmt_size: int = 18, extra_chunk: bytes = b""
) -> Path:
    """A WAV with a non-canonical layout: 18-byte fmt (as Windows SAPI and many
    encoders emit) and optionally a chunk sitting between fmt and data."""
    data = b"\x00\x01" * frames
    fmt_body = struct.pack(
        "<HHIIHH", 1, CH, SR, SR * CH * BITS // 8, CH * BITS // 8, BITS
    )
    fmt_body += b"\x00" * (fmt_size - 16)
    body = (
        b"fmt "
        + struct.pack("<I", fmt_size)
        + fmt_body
        + extra_chunk
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
    return path


def test_inspect_handles_18_byte_fmt_chunk(tmp_path):
    info = inspect(make_wav_ext(tmp_path / "ext.wav", SR))
    assert info.sample_rate == SR
    assert info.channels == CH
    assert info.bits_per_sample == BITS
    assert info.data_bytes == SR * 2
    assert info.duration_s == pytest.approx(1.0)


def test_repair_does_not_corrupt_a_healthy_extended_wav(tmp_path):
    p = make_wav_ext(tmp_path / "ext.wav", SR)
    before = p.read_bytes()
    assert repair_header(p) is False
    assert p.read_bytes() == before
    assert b"data" in p.read_bytes()[:64]


def test_repair_fixes_zeroed_data_size_in_extended_wav(tmp_path):
    p = make_wav_ext(tmp_path / "ext.wav", SR)
    raw = bytearray(p.read_bytes())
    data_at = raw.index(b"data")
    struct.pack_into("<I", raw, data_at + 4, 0)  # simula queda de energia
    p.write_bytes(bytes(raw))

    assert repair_header(p) is True
    assert inspect(p).data_bytes == SR * 2
    # o identificador do chunk continua intacto
    assert bytes(p.read_bytes())[data_at : data_at + 4] == b"data"


def test_inspect_skips_unknown_chunk_before_data(tmp_path):
    fact = b"fact" + struct.pack("<I", 4) + b"\x00\x00\x00\x00"
    info = inspect(make_wav_ext(tmp_path / "f.wav", SR, extra_chunk=fact))
    assert info.data_bytes == SR * 2
    assert info.sample_rate == SR


def test_rejects_wav_without_data_chunk(tmp_path):
    # Nome sem a palavra "data", senao o match do pytest casa com o caminho
    p = tmp_path / "silent.wav"
    fmt_body = struct.pack("<HHIIHH", 1, CH, SR, SR * 2, 2, BITS)
    # Padding generoso para o arquivo passar de 44 bytes e o erro ser de verdade
    # "nao ha chunk data", nao "arquivo curto demais".
    filler = b"LIST" + struct.pack("<I", 64) + b"\x00" * 64
    body = b"fmt " + struct.pack("<I", 16) + fmt_body + filler
    p.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
    with pytest.raises(InvalidWav, match="no 'data' chunk"):
        inspect(p)


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
