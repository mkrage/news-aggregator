#!/usr/bin/env python3
"""
Erzeugt favicon.svg und die PNG-Fallbacks aus einer gemeinsamen Beschreibung.

Das Mark ist eine Nachrichtenkarte: ein farbiger Schlagzeilen-Balken über zwei
weißen Textzeilen, in einem abgerundeten Quadrat. Bei 16 px bleibt davon ein
Akzentstreifen mit zwei Linien übrig – das liest sich noch als Text.

Keine externen Abhängigkeiten: das PNG wird direkt über zlib geschrieben.

Ausführen: python scripts/make_icons.py
"""

import os
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(__file__), "..")

# Geometrie im 64er-Raster (entspricht der SVG-viewBox).
CANVAS = 64
CORNER = 14.0
BARS = (
    # (x, y, breite, höhe, radius, farbschlüssel)
    (14.0, 18.0, 36.0, 7.0, 3.5, "headline"),
    (14.0, 30.0, 36.0, 5.0, 2.5, "line"),
    (14.0, 39.0, 22.0, 5.0, 2.5, "line"),
)

PALETTES = {
    "light": {"bg": (30, 58, 138), "headline": (147, 197, 253), "line": (255, 255, 255)},
    "dark": {"bg": (59, 130, 246), "headline": (219, 234, 254), "line": (255, 255, 255)},
}

SUPERSAMPLE = 4


def rounded_rect_coverage(px, py, x, y, w, h, radius):
    """1.0 innerhalb des abgerundeten Rechtecks, sonst 0.0 (Punktprobe)."""
    if not (x <= px <= x + w and y <= py <= y + h):
        return 0.0
    radius = min(radius, w / 2, h / 2)
    # Nur die vier Ecken müssen radial geprüft werden.
    cx = min(max(px, x + radius), x + w - radius)
    cy = min(max(py, y + radius), y + h - radius)
    return 1.0 if (px - cx) ** 2 + (py - cy) ** 2 <= radius**2 else 0.0


def render_rgba(size, palette, corner=CORNER):
    """Rendert das Mark mit Supersampling zu einem RGBA-Pixelpuffer."""
    scale = CANVAS / size
    step = 1.0 / SUPERSAMPLE
    offset = step / 2
    samples = SUPERSAMPLE * SUPERSAMPLE
    rows = []

    for row in range(size):
        pixels = bytearray()
        for col in range(size):
            bg_cov = 0.0
            bar_cov = {"headline": 0.0, "line": 0.0}

            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    px = (col + offset + sx * step) * scale
                    py = (row + offset + sy * step) * scale
                    bg_cov += rounded_rect_coverage(px, py, 0.0, 0.0, CANVAS, CANVAS, corner)
                    for bx, by, bw, bh, br, key in BARS:
                        bar_cov[key] += rounded_rect_coverage(px, py, bx, by, bw, bh, br)

            alpha = bg_cov / samples
            if alpha == 0.0:
                pixels += b"\x00\x00\x00\x00"
                continue

            # Balken über den Hintergrund legen (Bars liegen immer innerhalb).
            colour = [float(c) for c in palette["bg"]]
            for key in ("line", "headline"):
                weight = min(bar_cov[key] / samples, 1.0)
                if weight > 0.0:
                    target = palette[key]
                    colour = [c * (1 - weight) + target[i] * weight for i, c in enumerate(colour)]

            pixels += bytes(int(round(c)) for c in colour) + bytes([int(round(alpha * 255))])
        rows.append(bytes(pixels))
    return rows


def write_png(path, rows, size):
    """Minimaler PNG-Writer (RGBA, 8 Bit, Filter 0)."""
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")

    with open(path, "wb") as f:
        f.write(png)
    print(f"  {os.path.basename(path)} ({size}x{size}, {len(png)} Bytes)")


def rgb(colour):
    return "#%02x%02x%02x" % colour


def build_svg():
    """SVG mit Dark-Mode-Variante: auf dunkler Browserleiste wird das Mark heller."""
    light, dark = PALETTES["light"], PALETTES["dark"]
    bars = "\n".join(
        f'  <rect class="{key}" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="{r:g}"/>'
        for x, y, w, h, r, key in BARS
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {CANVAS}">
  <style>
    .bg {{ fill: {rgb(light["bg"])}; }}
    .headline {{ fill: {rgb(light["headline"])}; }}
    .line {{ fill: {rgb(light["line"])}; }}
    @media (prefers-color-scheme: dark) {{
      .bg {{ fill: {rgb(dark["bg"])}; }}
      .headline {{ fill: {rgb(dark["headline"])}; }}
    }}
  </style>
  <rect class="bg" width="{CANVAS}" height="{CANVAS}" rx="{CORNER:g}"/>
{bars}
</svg>
"""


def main():
    print("SVG:")
    svg_path = os.path.join(OUT_DIR, "favicon.svg")
    with open(svg_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_svg())
    print("  favicon.svg")

    print("PNG-Fallbacks:")
    # iOS und Android maskieren die Kachel selbst. Ein Icon mit transparenten
    # Ecken bekommt dort schwarze Ränder – deshalb randlos (corner=0).
    outputs = (
        (32, "favicon-32.png", CORNER),
        (180, "apple-touch-icon.png", 0.0),
        (512, "icon-512.png", 0.0),
    )
    for size, name, corner in outputs:
        rows = render_rgba(size, PALETTES["light"], corner)
        write_png(os.path.join(OUT_DIR, name), rows, size)


if __name__ == "__main__":
    main()
