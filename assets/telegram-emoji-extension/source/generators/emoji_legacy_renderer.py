"""Renderer reused from b630c3b; no legacy symbol generation or CLI entry point."""

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
