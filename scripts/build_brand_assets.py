"""Deterministic resize/letterbox only. Never crops, redraws or removes backgrounds.

Run: python scripts/build_brand_assets.py [--check]
Pillow is a development dependency; the application serves prebuilt files.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'bot/assets/brand'
SOURCE_SHA256 = '084ba43d6f5cd0ae8e51c6896db141dd9c78f0d80807917ef8917becaeda2337'
PRODUCTS = {
    'logo-512.png': (512, 512, 0),
    'logo-192.png': (192, 192, 0),
    'telegram-welcome.png': (1280, 720, 0),
    'description-640x360.png': (640, 360, 0),
    'avatar-1024.png': (1024, 1024, 0),
    'favicon-32.png': (32, 32, 2),
    'favicon-64.png': (64, 64, 4),
    'favicon-128.png': (128, 128, 8),
}


def contained(source, width, height, padding=0):
    resized = ImageOps.contain(source, (width-2*padding, height-2*padding),
                               Image.Resampling.LANCZOS)
    offset = ((width-resized.width)//2, (height-resized.height)//2)
    canvas = Image.new('RGB', (width, height), (0, 0, 0))
    canvas.paste(resized, offset)
    return canvas, [*offset, resized.width, resized.height]


def build():
    raw = (OUTPUT/'original.jpeg').read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('Owner source differs; review the source before regenerating')
    source = Image.open(BytesIO(raw)).convert('RGB')
    if source.size != (1254, 1254):
        raise ValueError('Unexpected source dimensions')
    files = {}
    manifest = dict(source='original.jpeg', source_sha256=SOURCE_SHA256,
                    source_size=list(source.size), transform='contain; centered; black letterbox; no crop',
                    products={})
    for name, (width, height, padding) in PRODUCTS.items():
        image, bounds = contained(source, width, height, padding)
        stream = BytesIO()
        image.save(stream, format='PNG', optimize=True)
        files[name] = stream.getvalue()
        manifest['products'][name] = dict(size=[width, height], source_bounds=bounds,
                                          sha256=hashlib.sha256(files[name]).hexdigest())
    icon, _ = contained(source, 128, 128, 8)
    stream = BytesIO()
    icon.save(stream, format='ICO', sizes=[(32, 32), (64, 64), (128, 128)])
    files['favicon.ico'] = stream.getvalue()
    manifest['products']['favicon.ico'] = dict(sizes=[[32, 32], [64, 64], [128, 128]],
                                               sha256=hashlib.sha256(files['favicon.ico']).hexdigest())
    files['manifest.json'] = (json.dumps(manifest, indent=2)+'\n').encode()
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    files = build()
    for name, data in files.items():
        path = OUTPUT/name
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                raise SystemExit('Brand asset differs: '+name)
        else:
            path.write_bytes(data)
    print('Brand assets: '+('verified' if args.check else 'built')+'; original SHA256 verified')


if __name__ == '__main__':
    main()
