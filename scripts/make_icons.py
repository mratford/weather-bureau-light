"""Draw the home-screen and browser icons.

Run after changing the artwork below:

    uv run python scripts/make_icons.py

The icons are committed, so this is not needed to run the app. iOS ignores SVG for
`apple-touch-icon` and fills transparent areas with black, so these are opaque PNGs.
They are drawn here rather than converted from templates/symbols.svg because the
available SVG renderer does not preserve the rotated rays.

Shapes are signed distance fields: negative inside and positive outside, measured in
pixels. Taking the minimum combines shapes, and the distance supplies the coverage
value for antialiased edges.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "weather_bureau_light" / "static"

# Keep this design consistent with templates/symbols.svg.
SUN = (0xF6, 0xA6, 0x23)
CLOUD = (0xD7, 0xDE, 0xE7)
CLOUD_EDGE = (0x9F, 0xAD, 0xBF)
PAGE = (0xFF, 0xFF, 0xFF)

# Describe the artwork on the same 24x24 grid as the weather symbols, then scale it
# to the requested output size.
GRID = 24.0

# The shapes are positioned relative to one another, then shifted to centre the drawing.
# The bounds run from the top-left ray (1.7, 1.5) to the cloud's lower right (18.5, 18.1).
ART_DX = 1.9
ART_DY = 2.2


def circle(px: float, py: float, cx: float, cy: float, r: float) -> float:
    return math.hypot(px - cx, py - cy) - r


def capsule(px, py, ax, ay, bx, by, r) -> float:
    """Return a thick line with rounded ends for a sun ray."""
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    t = 0.0 if span == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - ax - t * dx, py - ay - t * dy) - r


def rounded_box(px, py, cx, cy, half_w, half_h, r) -> float:
    qx = abs(px - cx) - (half_w - r)
    qy = abs(py - cy) - (half_h - r)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - r


def sun_field(x: float, y: float) -> float:
    """Return a disc with eight spokes."""
    cx, cy, r = 8.4, 8.2, 3.3
    d = circle(x, y, cx, cy, r)
    for step in range(8):
        angle = math.radians(step * 45)
        ux, uy = math.cos(angle), math.sin(angle)
        d = min(
            d,
            capsule(x, y, cx + ux * 4.5, cy + uy * 4.5, cx + ux * 6.3, cy + uy * 6.3, 0.42),
        )
    return d


def cloud_field(x: float, y: float) -> float:
    """Return three billows over a flat-bottomed base as one silhouette.

    Every part must reach the same bottom edge at y=18.1. Otherwise the outline can
    show a notch where the shapes meet.
    """
    d = circle(x, y, 11.4, 13.5, 3.9)
    d = min(d, circle(x, y, 15.6, 15.2, 2.9))
    d = min(d, circle(x, y, 7.9, 15.2, 2.9))
    return min(d, rounded_box(x, y, 11.7, 16.2, 5.9, 1.9, 1.4))


def blend(dst: list[float], index: int, colour: tuple[int, int, int], alpha: float) -> None:
    if alpha <= 0:
        return
    for channel in range(3):
        dst[index + channel] += (colour[channel] - dst[index + channel]) * alpha


def coverage(distance: float, feather: float) -> float:
    """Return the fraction of a pixel covered by the shape."""
    return max(0.0, min(1.0, 0.5 - distance / feather))


def render(size: int, background: tuple[int, int, int]) -> bytes:
    scale = size / GRID
    feather = 1.0 / scale  # One pixel in grid units.
    stroke = 0.22  # Half the cloud outline width.

    pixels = [0.0] * (size * size * 3)
    for i in range(0, len(pixels), 3):
        for channel in range(3):
            pixels[i + channel] = float(background[channel])

    for row in range(size):
        y = (row + 0.5) / scale
        base = row * size * 3
        for col in range(size):
            x = (col + 0.5) / scale
            index = base + col * 3
            ax, ay = x - ART_DX, y - ART_DY

            blend(pixels, index, SUN, coverage(sun_field(ax, ay), feather))

            cloud = cloud_field(ax, ay)
            blend(pixels, index, CLOUD, coverage(cloud, feather))
            # Centre the outline on the silhouette edge so the joins are hidden.
            blend(pixels, index, CLOUD_EDGE, coverage(abs(cloud) - stroke, feather))

    raw = bytearray()
    for row in range(size):
        raw.append(0)  # PNG filter type 0: none.
        start = row * size * 3
        raw.extend(int(round(v)) & 0xFF for v in pixels[start : start + size * 3])
    return bytes(raw)


def write_png(path: Path, size: int, raw: bytes) -> None:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # Use 8-bit truecolour without alpha.
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    # Use the sizes required by iOS, the manifest, and the browser tab.
    for name, size in (
        ("apple-touch-icon.png", 180),
        ("icon-192.png", 192),
        ("icon-512.png", 512),
        ("favicon-32.png", 32),
    ):
        target = STATIC / name
        write_png(target, size, render(size, PAGE))
        print(f"wrote {target.relative_to(STATIC.parents[2])} ({size}x{size})")


if __name__ == "__main__":
    main()
