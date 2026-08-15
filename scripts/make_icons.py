"""Draw the home-screen and browser icons.

Run after changing the artwork below:

    uv run python scripts/make_icons.py

The icons are committed, so this is not needed to run the app. iOS ignores SVG for
`apple-touch-icon` and will not composite a transparent one — it fills the gaps with
black — so these are opaque PNGs, drawn here rather than converted from the sprite in
templates/symbols.svg. ImageMagick's built-in SVG renderer mangles that file (it drops
the rotated rays), and a real SVG rasteriser is not something this project should need
installed just to produce four small images.

Shapes are signed distance fields: negative inside, positive outside, measured in pixels.
Combining them with min() unions them, and the distance doubles as the coverage value an
antialiased edge needs, which is what keeps the curves smooth without supersampling.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "weather_bureau_light" / "static"

# Matches the sprite in templates/symbols.svg, so the icon and the page agree.
SUN = (0xF6, 0xA6, 0x23)
CLOUD = (0xD7, 0xDE, 0xE7)
CLOUD_EDGE = (0x9F, 0xAD, 0xBF)
PAGE = (0xFF, 0xFF, 0xFF)

# The artwork is described in a 24x24 grid, as the weather symbols are, and scaled to
# whatever size is being written.
GRID = 24.0

# The shapes below were laid out relative to each other rather than to the grid, so the
# finished drawing is shifted to sit centred. Its extent runs from the tip of the
# top-left ray (1.7, 1.5) to the cloud's lower right (18.5, 18.1).
ART_DX = 1.9
ART_DY = 2.2


def circle(px: float, py: float, cx: float, cy: float, r: float) -> float:
    return math.hypot(px - cx, py - cy) - r


def capsule(px, py, ax, ay, bx, by, r) -> float:
    """A thick line with rounded ends, for the sun's rays."""
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
    """A disc with eight spokes around it."""
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
    """Three billows over a flat-bottomed base, unioned into one silhouette.

    Every part has to reach exactly the same bottom edge at y=18.1. A tenth of a unit
    of disagreement puts a visible notch in the outline, because the stroke follows the
    union's true edge rather than any one shape's.
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
    """Fraction of a pixel the shape covers, from its distance to the edge."""
    return max(0.0, min(1.0, 0.5 - distance / feather))


def render(size: int, background: tuple[int, int, int]) -> bytes:
    scale = size / GRID
    feather = 1.0 / scale  # One pixel, expressed in grid units.
    stroke = 0.22  # Half the cloud outline's width.

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
            # The outline straddles the silhouette's edge, so it covers the join
            # between the billows without showing where they overlap.
            blend(pixels, index, CLOUD_EDGE, coverage(abs(cloud) - stroke, feather))

    raw = bytearray()
    for row in range(size):
        raw.append(0)  # PNG filter type 0 (none) for this scanline.
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

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)  # 8-bit truecolour, no alpha.
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    # 180 is what iOS asks for; 192 and 512 are the manifest sizes; 32 is the browser tab.
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
