"""Inspect and repair PCM WAV files written by the device.

Offsets are never hardcoded. The canonical 44-byte layout has a 16-byte `fmt `
chunk, but Windows SAPI and many encoders emit 18 bytes (a `cbSize` extension),
and some writers insert a `fact` or `LIST` chunk before `data`. Patching a fixed
offset 40 in those files overwrites the middle of the `data` chunk header and
destroys the recording, so both functions here locate chunks by walking them.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

RIFF_HEADER_SIZE = 12
_RIFF_SIZE_OFFSET = 4
_CHUNK_HEADER_SIZE = 8


class InvalidWav(Exception):
    """Raised when a file is not a usable PCM WAV."""


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


@dataclass(frozen=True)
class _Layout:
    """Where the chunks actually live in this particular file."""

    fmt_body_offset: int
    data_size_offset: int
    data_body_offset: int
    declared_data_bytes: int


def _read_all(path: Path) -> bytes:
    raw = Path(path).read_bytes()
    if len(raw) < RIFF_HEADER_SIZE:
        raise InvalidWav(f"file shorter than a RIFF header: {path}")
    if raw[0:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise InvalidWav(f"missing RIFF/WAVE magic: {path}")
    return raw


def _layout(raw: bytes, path: Path) -> _Layout:
    fmt_body_offset: int | None = None
    offset = RIFF_HEADER_SIZE
    while offset + _CHUNK_HEADER_SIZE <= len(raw):
        chunk_id = raw[offset : offset + 4]
        (size,) = struct.unpack_from("<I", raw, offset + 4)
        body = offset + _CHUNK_HEADER_SIZE

        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(raw):
                raise InvalidWav(f"truncated 'fmt ' chunk: {path}")
            fmt_body_offset = body
        elif chunk_id == b"data":
            if fmt_body_offset is None:
                raise InvalidWav(f"'data' chunk precedes 'fmt ': {path}")
            return _Layout(
                fmt_body_offset=fmt_body_offset,
                data_size_offset=offset + 4,
                data_body_offset=body,
                declared_data_bytes=size,
            )

        # A zero-size unknown chunk would spin forever; advance at least one header.
        step = _CHUNK_HEADER_SIZE + size + (size & 1)
        offset += max(step, _CHUNK_HEADER_SIZE)

    raise InvalidWav(f"no 'data' chunk found: {path}")


def inspect(path: Path) -> WavInfo:
    path = Path(path)
    raw = _read_all(path)
    layout = _layout(raw, path)

    channels, sample_rate = struct.unpack_from("<HI", raw, layout.fmt_body_offset + 2)
    (bits_per_sample,) = struct.unpack_from("<H", raw, layout.fmt_body_offset + 14)

    available = len(raw) - layout.data_body_offset
    data_bytes = layout.declared_data_bytes
    # Trust the file over the header: a truncated recording declares more than it has,
    # and an unclosed one declares zero.
    if data_bytes == 0 or data_bytes > available:
        data_bytes = max(available, 0)
    return WavInfo(sample_rate, channels, bits_per_sample, data_bytes)


def repair_header(path: Path) -> bool:
    """Patch a zeroed or overlong data size from the real file size.

    Returns True if the file was modified. Never touches chunk identifiers.
    """
    path = Path(path)
    raw = _read_all(path)
    layout = _layout(raw, path)

    available = len(raw) - layout.data_body_offset
    if available <= 0:
        raise InvalidWav(f"no audio payload: {path}")
    if layout.declared_data_bytes == available:
        return False

    with path.open("r+b") as fh:
        fh.seek(_RIFF_SIZE_OFFSET)
        fh.write(struct.pack("<I", len(raw) - _CHUNK_HEADER_SIZE))
        fh.seek(layout.data_size_offset)
        fh.write(struct.pack("<I", available))
    return True
