#!/usr/bin/env python3
"""Generate KudaVPN Premium custom emoji from one vector-like geometry source.

The SVG files are editable design sources. PNG files are static fallbacks and
VP9 WebM files are the Telegram custom-emoji exports. No network is used.
"""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1] / "assets" / "telegram-emoji"
SIZE = 100
SCALE = 3
FPS = 24
FRAMES = 48
CYAN = "#55D6FF"
ICE = "#D9F7FF"
MUTED = "#6594A6"
GRAPHITE = "#111A22"
EDGE = "#315165"


@dataclass(frozen=True)
class Mark:
    name: str
    label: str
    alt: str
    summary: str


MARKS = [
    Mark("connect", "Подключение", "🔗", "Сомкнутые звенья и линия канала"),
    Mark("protect", "Защита", "🛡️", "Щит с защищённым ядром"),
    Mark("premium", "Premium", "💎", "Огранённый знак KudaVPN"),
    Mark("subscription", "Подписка", "🎟️", "Карточка доступа с проверкой"),
    Mark("plans", "Тарифы", "📊", "Три ступени тарифа"),
    Mark("devices", "Устройства", "📱", "Телефон и ноутбук"),
    Mark("payment", "Оплата", "💳", "Платёжная карта"),
    Mark("renew", "Продление", "🔄", "Циклическая стрелка"),
    Mark("support", "Поддержка", "🎧", "Гарнитура поддержки"),
    Mark("gift", "Подарок", "🎁", "Коробка с лентой"),
    Mark("server", "Сервер", "🖥️", "Два серверных модуля"),
    Mark("status", "Статус", "🟢", "Радиальный индикатор"),
    Mark("speed", "Скорость", "⚡", "Электрический импульс"),
    Mark("world", "Локации", "🌐", "Глобус с меридианом"),
]


class Graphic:
    def __init__(self) -> None:
        self.shapes: list[tuple[str, tuple, str, float, str | None]] = []

    def line(self, points: list[tuple[float, float]], color: str = ICE, width: float = 3.6) -> None:
        self.shapes.append(("line", tuple(points), color, width, None))

    def circle(self, x: float, y: float, r: float, color: str = CYAN, width: float = 3.4, fill: str | None = None) -> None:
        self.shapes.append(("circle", (x, y, r), color, width, fill))

    def rect(self, box: tuple[float, float, float, float], radius: float = 0, color: str = ICE,
             width: float = 3.3, fill: str | None = None) -> None:
        self.shapes.append(("rect", (*box, radius), color, width, fill))

    def arc(self, box: tuple[float, float, float, float], start: float, end: float,
            color: str = ICE, width: float = 3.5) -> None:
        self.shapes.append(("arc", (*box, start, end), color, width, None))

    def poly(self, points: list[tuple[float, float]], color: str = ICE,
             width: float = 3.3, fill: str | None = None) -> None:
        self.shapes.append(("poly", tuple(points), color, width, fill))


def symbol(name: str) -> Graphic:
    g = Graphic()
    if name == "connect":
        g.arc((26, 32, 58, 66), 64, 292)
        g.arc((42, 34, 74, 68), 244, 472)
        g.line([(39, 51), (61, 49)], CYAN, 4)
    elif name == "protect":
        g.poly([(50, 27), (69, 35), (66, 59), (50, 72), (34, 59), (31, 35), (50, 27)])
        g.line([(41, 50), (48, 57), (60, 43)], CYAN, 4)
    elif name == "premium":
        g.poly([(33, 43), (42, 31), (58, 31), (67, 43), (50, 69), (33, 43)])
        g.line([(33, 43), (67, 43)], CYAN, 2.7)
        g.line([(42, 31), (50, 43), (58, 31)], MUTED, 2.5)
        g.line([(50, 43), (50, 67)], CYAN, 2.7)
    elif name == "subscription":
        g.rect((29, 32, 71, 67), 7)
        g.line([(39, 42), (54, 42)], CYAN, 3)
        g.line([(39, 51), (49, 51)], MUTED, 3)
        g.line([(53, 57), (58, 62), (66, 51)], CYAN, 3.4)
    elif name == "plans":
        g.line([(31, 68), (69, 68)], MUTED, 3)
        for x, top in [(34, 54), (47, 43), (60, 33)]:
            g.rect((x, top, x + 7, 65), 2.5, CYAN if x == 60 else ICE, 2.3)
    elif name == "devices":
        g.rect((30, 35, 55, 59), 3.5)
        g.line([(27, 63), (57, 63)], CYAN, 3)
        g.rect((57, 39, 70, 66), 3, ICE, 2.7)
        g.circle(63.5, 61.5, 0.8, CYAN, 1.4, CYAN)
    elif name == "payment":
        g.rect((27, 36, 73, 64), 6)
        g.line([(30, 45), (70, 45)], CYAN, 4)
        g.line([(35, 56), (46, 56)], ICE, 3)
        g.circle(63, 56, 2, CYAN, 2, CYAN)
    elif name == "renew":
        g.arc((31, 31, 69, 69), 205, 515, ICE, 3.8)
        g.poly([(69, 41), (69, 53), (59, 47)], CYAN, 2, CYAN)
    elif name == "support":
        g.arc((31, 31, 69, 67), 184, 356, ICE, 3.7)
        g.rect((29, 49, 36, 62), 3, CYAN, 2.4)
        g.rect((64, 49, 71, 62), 3, CYAN, 2.4)
        g.arc((45, 54, 68, 70), 2, 90, MUTED, 2.7)
        g.circle(46, 68, 2, CYAN, 2, CYAN)
    elif name == "gift":
        g.rect((30, 45, 70, 68), 3)
        g.rect((27, 39, 73, 46), 2.5, CYAN, 2.7)
        g.line([(50, 41), (50, 67)], CYAN, 3)
        g.arc((34, 27, 51, 42), 0, 188, ICE, 3)
        g.arc((49, 27, 66, 42), 352, 540, ICE, 3)
    elif name == "server":
        for top in (31, 51):
            g.rect((29, top, 71, top + 16), 4)
            g.circle(37, top + 8, 2, CYAN, 2, CYAN)
            g.line([(48, top + 8), (64, top + 8)], MUTED, 2.5)
    elif name == "status":
        g.arc((30, 30, 70, 70), 210, 510, ICE, 3.5)
        g.arc((38, 38, 62, 62), 210, 510, MUTED, 2.7)
        g.circle(50, 50, 5.8, CYAN, 2, CYAN)
    elif name == "speed":
        g.poly([(54, 28), (37, 52), (48, 52), (44, 72), (64, 45), (52, 45), (54, 28)],
               CYAN, 3.4, "#21465C")
        g.line([(28, 62), (36, 62)], MUTED, 2.8)
    elif name == "world":
        g.circle(50, 50, 21, ICE, 3)
        g.arc((40, 29, 60, 71), 90, 270, CYAN, 2.7)
        g.arc((40, 29, 60, 71), 270, 450, CYAN, 2.7)
        g.line([(30, 50), (70, 50)], MUTED, 2.6)
    else:
        raise ValueError(name)
    return g


def hex_rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return (*tuple(bytes.fromhex(color[1:])), alpha)


def scaled(points: tuple | list) -> list[tuple[int, int]]:
    return [(round(x * SCALE), round(y * SCALE)) for x, y in points]


def draw_shape(draw: ImageDraw.ImageDraw, shape: tuple, *, glow: bool = False) -> None:
    kind, data, stroke, width, fill = shape
    color = hex_rgba(stroke, 110 if glow else 255)
    width_px = round(width * SCALE * (2.2 if glow else 1))
    if kind == "line":
        points = scaled(data)
        draw.line(points, fill=color, width=width_px, joint="curve")
        if not glow:
            r = width_px / 2
            for x, y in (points[0], points[-1]):
                draw.ellipse((x-r, y-r, x+r, y+r), fill=color)
    elif kind == "circle":
        x, y, r = data
        box = tuple(round(v * SCALE) for v in (x-r, y-r, x+r, y+r))
        draw.ellipse(box, outline=color, width=width_px,
                     fill=hex_rgba(fill, 110 if glow else 255) if fill else None)
    elif kind == "rect":
        x1, y1, x2, y2, radius = data
        box = tuple(round(v * SCALE) for v in (x1, y1, x2, y2))
        draw.rounded_rectangle(box, radius=round(radius*SCALE), outline=color, width=width_px,
                               fill=hex_rgba(fill, 110 if glow else 255) if fill else None)
    elif kind == "arc":
        x1, y1, x2, y2, start, end = data
        box = tuple(round(v * SCALE) for v in (x1, y1, x2, y2))
        draw.arc(box, start, end, fill=color, width=width_px)
    elif kind == "poly":
        points = scaled(data)
        if fill:
            draw.polygon(points, fill=hex_rgba(fill, 110 if glow else 255))
        draw.line(points, fill=color, width=width_px, joint="curve")


def frame(g: Graphic, tick: int) -> Image.Image:
    canvas = Image.new("RGBA", (SIZE*SCALE, SIZE*SCALE))
    base = ImageDraw.Draw(canvas)
    base.rounded_rectangle((9*SCALE, 9*SCALE, 91*SCALE, 91*SCALE), radius=23*SCALE,
                           fill=hex_rgba(GRAPHITE), outline=hex_rgba(EDGE), width=2*SCALE)
    base.arc((18*SCALE, 18*SCALE, 82*SCALE, 82*SCALE), 205, 335,
             fill=hex_rgba("#25475A"), width=2*SCALE)
    glow = Image.new("RGBA", canvas.size)
    glow_draw = ImageDraw.Draw(glow)
    for shape in g.shapes:
        if shape[2] == CYAN:
            draw_shape(glow_draw, shape, glow=True)
    theta = -math.pi/2 + 2*math.pi*tick/FRAMES
    x, y = 50 + 34*math.cos(theta), 50 + 34*math.sin(theta)
    orb = ("circle", (x, y, 2.3), CYAN, 1.4, CYAN)
    draw_shape(glow_draw, orb, glow=True)
    canvas = Image.alpha_composite(canvas, glow.filter(ImageFilter.GaussianBlur(3.2*SCALE)))
    ink = Image.new("RGBA", canvas.size)
    ink_draw = ImageDraw.Draw(ink)
    for shape in g.shapes:
        draw_shape(ink_draw, shape)
    draw_shape(ink_draw, orb)
    canvas = Image.alpha_composite(canvas, ink)
    return canvas.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def svg_shape(shape: tuple) -> str:
    kind, data, stroke, width, fill = shape
    attrs = f'stroke="{stroke}" stroke-width="{width}" fill="{fill or "none"}"'
    if kind == "line":
        points = " ".join(f"{x},{y}" for x, y in data)
        return f'<polyline points="{points}" {attrs}/>'
    if kind == "circle":
        x, y, r = data
        return f'<circle cx="{x}" cy="{y}" r="{r}" {attrs}/>'
    if kind == "rect":
        x1, y1, x2, y2, radius = data
        return f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="{radius}" {attrs}/>'
    if kind == "arc":
        x1, y1, x2, y2, start, end = data
        cx, cy = (x1+x2)/2, (y1+y2)/2
        rx, ry = (x2-x1)/2, (y2-y1)/2
        a, b = math.radians(start), math.radians(end)
        p1 = (cx+rx*math.cos(a), cy+ry*math.sin(a))
        p2 = (cx+rx*math.cos(b), cy+ry*math.sin(b))
        large = 1 if end-start > 180 else 0
        return f'<path d="M {p1[0]:.3f} {p1[1]:.3f} A {rx} {ry} 0 {large} 1 {p2[0]:.3f} {p2[1]:.3f}" {attrs}/>'
    if kind == "poly":
        points = " ".join(f"{x},{y}" for x, y in data)
        return f'<polygon points="{points}" {attrs}/>'
    raise ValueError(kind)


def write_svg(mark: Mark, g: Graphic, path: Path) -> None:
    shapes = "\n    ".join(svg_shape(s) for s in g.shapes)
    path.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100" role="img" aria-label="{escape(mark.label)}">
  <title>KudaVPN Premium — {escape(mark.label)}</title>
  <defs><filter id="glow" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="2.5"/></filter></defs>
  <rect x="9" y="9" width="82" height="82" rx="23" fill="{GRAPHITE}" stroke="{EDGE}" stroke-width="2"/>
  <path d="M 21.6 63.5 A 32 32 0 0 1 78.4 36.5" fill="none" stroke="#25475A" stroke-width="2"/>
  <g stroke-linecap="round" stroke-linejoin="round">
    {shapes}
  </g>
  <circle cx="0" cy="0" r="5" fill="{CYAN}" opacity="0.35" filter="url(#glow)">
    <animateMotion dur="2s" repeatCount="indefinite" path="M 50 16 A 34 34 0 1 1 49.99 16"/>
  </circle>
  <circle cx="0" cy="0" r="2.3" fill="{CYAN}">
    <animateMotion dur="2s" repeatCount="indefinite" path="M 50 16 A 34 34 0 1 1 49.99 16"/>
  </circle>
</svg>
''', encoding="utf-8")


def ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise SystemExit("Install imageio-ffmpeg and Pillow in a local build environment") from exc
    return imageio_ffmpeg.get_ffmpeg_exe()


def export(mark: Mark, ffmpeg: str) -> None:
    g = symbol(mark.name)
    source = ROOT / "source"
    video = ROOT / "webm"
    static = ROOT / "png"
    for directory in (source, video, static):
        directory.mkdir(parents=True, exist_ok=True)
    write_svg(mark, g, source / f"{mark.name}.svg")
    frame(g, 0).save(static / f"{mark.name}.png", optimize=True)
    output = video / f"{mark.name}.webm"
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo",
           "-pix_fmt", "rgba", "-s", f"{SIZE}x{SIZE}", "-r", str(FPS), "-i", "-",
           "-an", "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-auto-alt-ref", "0",
           "-b:v", "0", "-crf", "36", "-deadline", "good", "-cpu-used", "3", str(output)]
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    for tick in range(FRAMES):
        process.stdin.write(frame(g, tick).tobytes())
    process.stdin.close()
    assert process.stderr is not None
    errors = process.stderr.read().decode("utf-8", "replace")
    if process.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for {mark.name}: {errors[-400:]}")
    if output.stat().st_size > 256_000:
        raise RuntimeError(f"{mark.name} exceeds Telegram's 256 KB limit")
    print(f"{mark.name}: {output.stat().st_size} bytes")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=[m.name for m in MARKS])
    args = parser.parse_args()
    ffmpeg = ffmpeg_path()
    for mark in MARKS:
        if args.only is None or args.only == mark.name:
            export(mark, ffmpeg)


if __name__ == "__main__":
    main()
