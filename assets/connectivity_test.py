"""Generate the setup connectivity image using only the Python standard library."""

from __future__ import annotations

import argparse
import binascii
import os
import struct
import zlib
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def _pixel(x: int, y: int, width: int, height: int) -> tuple[int, int, int]:
    if y < height // 5:
        return (23, 43, 58)
    margin_x = width // 18
    margin_y = height // 3
    if margin_x <= x < width - margin_x and margin_y <= y < height - height // 7:
        if x < width // 3:
            return (33, 166, 116)
        if x < width // 3 + width // 35:
            return (17, 116, 82)
        stripe = ((x // 18) + (y // 12)) % 2
        return (234, 246, 240) if stripe else (214, 237, 225)
    border = x in {margin_x, width - margin_x - 1} or y in {margin_y, height - height // 7 - 1}
    if border and margin_x <= x < width - margin_x and margin_y <= y < height - height // 7:
        return (124, 148, 158)
    return (248, 250, 249)


def write_connectivity_png(path: Path, *, width: int = 400, height: int = 140) -> Path:
    if width < 320 or height < 120:
        raise ValueError("connectivity PNG must be at least 320x120")
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(_pixel(x, y, width, height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    payload = b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", zlib.compress(bytes(rows), level=9)),
            _chunk(b"IEND", b""),
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the dealy-report connectivity PNG")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_connectivity_png(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
