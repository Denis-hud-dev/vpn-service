#!/usr/bin/env python3
"""Build a local, secret-free preview of the custom emoji pack."""

from __future__ import annotations

from html import escape
import json
from pathlib import Path
from dataclasses import asdict

from PIL import Image, ImageDraw, ImageFont

from generate_premium_emoji import MARKS, ROOT, frame, symbol


PREVIEW = ROOT / "preview"
PREVIEW.mkdir(parents=True, exist_ok=True)
(ROOT / "manifest.json").write_text(json.dumps({
    "name": "KudaVPN Premium / Quiet Current",
    "status": "demo-not-published",
    "telegram_type": "custom_emoji",
    "format": "video/webm",
    "duration_seconds": 2,
    "fps": 24,
    "size_px": [100, 100],
    "palette": {"graphite": "#111A22", "electric_cyan": "#55D6FF", "ice": "#D9F7FF"},
    "icons": [asdict(mark) for mark in MARKS],
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

cards = []
for mark in MARKS:
    cards.append(f'''<article class="card">
      <div class="icon"><video autoplay muted loop playsinline preload="metadata" poster="../png/{mark.name}.png"><source src="../webm/{mark.name}.webm" type="video/webm"></video></div>
      <div class="name">{escape(mark.label)}</div>
      <div class="detail">{escape(mark.summary)}</div>
      <div class="slug">{mark.name}.webm</div>
    </article>''')

(PREVIEW / "index.html").write_text('''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KudaVPN Premium — Emoji System</title>
<style>
:root{color-scheme:dark;font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#090e14;color:#e4f5fb}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 65% -12%,#143447 0,#090e14 49%);min-height:100vh}
main{max-width:1170px;margin:auto;padding:48px 24px 70px}.eyebrow{letter-spacing:.22em;color:#55d6ff;text-transform:uppercase;font-size:11px;font-weight:800}
h1{font-size:clamp(34px,5vw,62px);letter-spacing:-.055em;line-height:1.02;margin:16px 0}.lead{max-width:620px;color:#96afbb;line-height:1.6;margin-bottom:36px}
.meta{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:35px}.pill{border:1px solid #2d5062;background:#12222d;border-radius:999px;color:#9bd9ef;padding:8px 13px;font-size:12px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.card{min-height:220px;padding:23px;border:1px solid #243a48;border-radius:23px;background:linear-gradient(155deg,#16242e,#10181f);box-shadow:0 12px 24px #0002;position:relative;overflow:hidden}
.card::after{content:"";position:absolute;width:120px;height:120px;right:-45px;top:-55px;background:#55d6ff0d;filter:blur(26px);border-radius:50%}
.icon{height:103px;display:flex;align-items:center}.icon video{width:100px;height:100px;object-fit:contain}.name{font-size:17px;font-weight:650;margin-top:5px}.detail{font-size:12px;color:#8fa7b4;line-height:1.45;margin-top:7px}.slug{font:10px ui-monospace,monospace;color:#548198;margin-top:14px}
footer{margin-top:28px;color:#657f8d;font-size:12px}
@media(max-width:850px){.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:620px){main{padding:32px 16px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.card{min-height:198px;padding:15px;border-radius:18px}.detail{font-size:11px}}
@media(prefers-reduced-motion:reduce){video{animation:none}}
</style></head><body><main>
<div class="eyebrow">KudaVPN / Premium / Design system 01</div><h1>Тихая энергия<br>под защитой.</h1>
<p class="lead">Четырнадцать фирменных анимированных знаков для Telegram. Графитовый корпус, электрический голубой и спокойное двухсекундное движение без вспышек.</p>
<div class="meta"><span class="pill">14 знаков</span><span class="pill">100 × 100 px</span><span class="pill">VP9 · WebM</span><span class="pill">2 секунды · 24 кадра/с</span></div>
<section class="grid">''' + "\n".join(cards) + '''</section>
<footer>Локальный демонстрационный просмотр. Набор ещё не опубликован в Telegram и не подключён к боту.</footer>
</main></body></html>''', encoding="utf-8")

width, height = 1120, 490
sheet = Image.new("RGB", (width, height), "#0a1016")
draw = ImageDraw.Draw(sheet)
font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
title_font = ImageFont.truetype(font_path, 28)
label_font = ImageFont.truetype(font_path, 15)
meta_font = ImageFont.truetype(font_path, 12)
draw.text((29, 22), "KudaVPN Premium / animated emoji", font=title_font, fill="#e4f5fb")
draw.text((31, 62), "14 original marks  •  electric cyan  •  graphite  •  2 s seamless loop", font=meta_font, fill="#80a5b7")
for index, mark in enumerate(MARKS):
    col, row = index % 7, index // 7
    x, y = 23 + col * 157, 102 + row * 190
    draw.rounded_rectangle((x, y, x + 146, y + 176), radius=19, fill="#14212b", outline="#29475a", width=2)
    image = Image.open(ROOT / "png" / f"{mark.name}.png").convert("RGBA")
    sheet.paste(image, (x + 23, y + 18), image)
    draw.text((x + 14, y + 128), mark.label, font=label_font, fill="#d9f7ff")
    draw.text((x + 14, y + 152), mark.name, font=meta_font, fill="#5b96ae")
sheet.save(PREVIEW / "contact-sheet.png", optimize=True)
animated = []
graphics = {mark.name: symbol(mark.name) for mark in MARKS}
for tick in range(0, 48, 2):
    picture = sheet.copy()
    for index, mark in enumerate(MARKS):
        col, row = index % 7, index // 7
        x, y = 23 + col * 157, 102 + row * 190
        icon = frame(graphics[mark.name], tick)
        # Refill the icon area so the previous frame never persists.
        area = Image.new("RGB", (100, 100), "#14212b")
        area.paste(icon, (0, 0), icon)
        picture.paste(area, (x + 23, y + 18))
    animated.append(picture.quantize(colors=96))
animated[0].save(PREVIEW / "animated-grid.gif", save_all=True, append_images=animated[1:],
                 duration=83, loop=0, optimize=True, disposal=2)
print(PREVIEW / "index.html")
print(PREVIEW / "contact-sheet.png")
print(PREVIEW / "animated-grid.gif")
