"""Render assets/icon.svg into the icon files each platform wants (needs PySide6; run when the SVG changes).

    uv run python packaging/make_icons.py

Writes assets/icon.png (512 px, Linux and the README), assets/icon.ico (Windows, 16-256 px) and assets/icon.icns (macOS, 16-1024 px,
with the transparent margin macOS icons have). The ICO and ICNS containers are written here directly (PNG payloads), so no platform tools
are needed.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def render(svg: Path, size: int, margin: float = 0.0) -> bytes:
    """PNG bytes of the SVG at ``size`` px, with a transparent ``margin`` (fraction of the size) on every side."""
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    m = size * margin
    QSvgRenderer(str(svg)).render(p, QRectF(m, m, size - 2 * m, size - 2 * m))
    p.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(ba)


def write_ico(path: Path, pngs: dict[int, bytes]) -> None:
    """ICO with PNG-compressed entries (Windows Vista and later)."""
    sizes = sorted(pngs)
    out = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    body = b""
    for n in sizes:
        data = pngs[n]
        out += struct.pack("<BBBBHHII", n % 256, n % 256, 0, 0, 1, 32, len(data), offset + len(body))   # 256 is stored as 0
        body += data
    path.write_bytes(out + body)


def write_icns(path: Path, pngs: dict[int, bytes]) -> None:
    """ICNS with PNG chunks: icp4/5/6 (16, 32, 64) and ic07-ic10 (128, 256, 512, 1024)."""
    types = {16: b"icp4", 32: b"icp5", 64: b"icp6", 128: b"ic07", 256: b"ic08", 512: b"ic09", 1024: b"ic10"}
    chunks = b"".join(types[n] + struct.pack(">I", 8 + len(pngs[n])) + pngs[n] for n in sorted(types))
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(chunks)) + chunks)


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)         # noqa: F841 (the renderer needs an application)
    svg = ASSETS / "icon.svg"
    (ASSETS / "icon.png").write_bytes(render(svg, 512))
    write_ico(ASSETS / "icon.ico", {n: render(svg, n) for n in (16, 24, 32, 48, 64, 128, 256)})
    write_icns(ASSETS / "icon.icns", {n: render(svg, n, margin=0.1) for n in (16, 32, 64, 128, 256, 512, 1024)})
    for f in ("icon.png", "icon.ico", "icon.icns"):
        print(f"{f}: {(ASSETS / f).stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
