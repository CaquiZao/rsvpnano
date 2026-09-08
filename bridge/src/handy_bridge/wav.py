"""Inspect and repair canonical PCM WAV files written by the device."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

HEADER_SIZE = 44
_RIFF_SIZE_OFFSET = 4
_DATA_SIZE_OFFSET = 40


class InvalidWav(Exception):
    """Raised when a file is not a usable 44-byte-header PCM WAV."""


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    bits_per_sample: int
    data_bytes: int

    @property
    def duration_s(self) -> float:
        frame_bytes = self.channels * self.bits_per_sample // 8
        if frame_bytes == 0 or self.sample_rate == 0:
            return 0.0
        return self.data_bytes / frame_bytes / self.sample_rate


def _read_header(path: Path) -> bytes:
    raw = Path(path).read_bytes()[:HEADER_SIZE]
    if len(raw) < HEADER_SIZE:
        raise InvalidWav(f"file shorter than a WAV header: {path}")
    if raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise InvalidWav(f"missing RIFF/WAVE magic: {path}")
    return raw


def inspect(path: Path) -> WavInfo:
    raw = _read_header(path)
    channels, sample_rate = struct.unpack_from("<HI", raw, 22)
    (bits_per_sample,) = struct.unpack_from("<H", raw, 34)
    (data_bytes,) = struct.unpack_from("<I", raw, _DATA_SIZE_OFFSET)
    actual = Path(path).stat().st_size - HEADER_SIZE
    # Trust the file over the header: a truncated recording reports more than it has.
    if data_bytes == 0 or data_bytes > actual:
        data_bytes = max(actual, 0)
    return WavInfo(sample_rate, channels, bits_per_sample, data_bytes)


def repair_header(path: Path) -> bool:
    """Patch zeroed RIFF/data sizes from the real file size. Returns True if patched."""
    path = Path(path)
    raw = _read_header(path)
    (declared,) = struct.unpack_from("<I", raw, _DATA_SIZE_OFFSET)
    actual = path.stat().st_size - HEADER_SIZE
    if actual <= 0:
        raise InvalidWav(f"no audio payload: {path}")
    if declared == actual:
        return False
    with path.open("r+b") as fh:
        fh.seek(_RIFF_SIZE_OFFSET)
        fh.write(struct.pack("<I", 36 + actual))
        fh.seek(_DATA_SIZE_OFFSET)
        fh.write(struct.pack("<I", actual))
    return True
