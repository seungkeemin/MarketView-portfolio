"""실행 아이콘(scripts/marketview.ico)을 만든다. 표준 라이브러리만 쓴다(PNG·ICO 를 직접 씀).

  .venv\\Scripts\\python scripts\\make_icon.py

화면의 시각 언어를 따른다: 흰 배경, 강조 파랑 #2F5FB3 왼쪽 바, 막대 세 줄. 그림자·그라디언트 없음.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

BG = (255, 255, 255, 255)
ACCENT = (0x2F, 0x5F, 0xB3, 255)
GRAY = (0x6B, 0x77, 0x85, 255)
LINE = (0xCF, 0xD4, 0xDC, 255)
SIZES = (16, 32, 48, 64, 128, 256)

# (x0, x1, y0, y1, 색) — 한 변에 대한 비율
SHAPES = (
    (0.00, 1.00, 0.00, 0.06, LINE), (0.00, 1.00, 0.94, 1.00, LINE),
    (0.00, 0.125, 0.00, 1.00, ACCENT),
    (0.25, 0.875, 0.22, 0.34, GRAY),
    (0.25, 0.65, 0.44, 0.56, LINE),
    (0.25, 0.78, 0.66, 0.78, ACCENT),
)


def render(size: int) -> bytes:
    rows = [[BG] * size for _ in range(size)]
    for x0, x1, y0, y1, color in SHAPES:
        left, right = int(x0 * size), max(int(x1 * size), int(x0 * size) + 1)
        top, bottom = int(y0 * size), max(int(y1 * size), int(y0 * size) + 1)
        for y in range(top, min(bottom, size)):
            for x in range(left, min(right, size)):
                rows[y][x] = color
    return b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)


def png(size: int) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(render(size), 9)) + chunk(b"IEND", b""))


def ico() -> bytes:
    images = [png(s) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in zip(SIZES, images):
        out += struct.pack("<BBBBHHII", 0 if size >= 256 else size, 0 if size >= 256 else size, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


if __name__ == "__main__":
    target = Path(__file__).with_name("marketview.ico")
    target.write_bytes(ico())
    print(f"만들었습니다: {target} ({target.stat().st_size} bytes)")
