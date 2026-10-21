#!/usr/bin/env python3
"""Check Telegram WebM custom-emoji constraints and decode every export."""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET

from PIL import Image

from generate_premium_emoji import MARKS, ROOT, ffmpeg_path


def main() -> None:
    ffmpeg = ffmpeg_path()
    assert len(MARKS) == 14
    for mark in MARKS:
        webm = ROOT / "webm" / f"{mark.name}.webm"
        png = ROOT / "png" / f"{mark.name}.png"
        svg = ROOT / "source" / f"{mark.name}.svg"
        assert all(path.exists() for path in (webm, png, svg)), mark.name
        assert webm.stat().st_size <= 256_000, mark.name
        with Image.open(png) as image:
            assert image.size == (100, 100) and image.mode == "RGBA", mark.name
        root = ET.parse(svg).getroot()
        assert root.attrib.get("viewBox") == "0 0 100 100", mark.name

        probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(webm)],
                               capture_output=True, text=True, check=False).stderr
        assert "Video: vp9" in probe and "100x100" in probe, mark.name
        assert "24 fps" in probe and "Duration: 00:00:02.00" in probe, mark.name
        assert "alpha_mode      : 1" in probe and "Audio:" not in probe, mark.name

        # FFmpeg's native VP9 decoder can discard alpha; use libvpx to inspect it.
        decoded = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-c:v", "libvpx-vp9", "-i", str(webm),
                                  "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                                 capture_output=True, check=True).stdout
        frame_bytes = 100 * 100 * 4
        assert len(decoded) == frame_bytes * 48, (mark.name, len(decoded) // frame_bytes)
        assert decoded[3] == 0 and decoded[(50*100+50)*4+3] == 255, mark.name
        assert decoded[:frame_bytes] != decoded[24*frame_bytes:25*frame_bytes], mark.name
        print(f"{mark.name}: VP9, alpha, 100x100, 24 fps, 2 s, 48 moving frames, {webm.stat().st_size} B")
    print("14/14 Telegram video custom emoji constraints passed")


if __name__ == "__main__":
    main()
