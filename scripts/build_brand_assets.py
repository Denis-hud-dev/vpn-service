"""Resize approved RGBA artwork; extract the authorized compact K from its pixels.

Run: python scripts/build_brand_assets.py [--check]
Pillow is a development dependency; the application serves prebuilt files.
"""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageOps, ImageDraw, ImageChops, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'bot/assets/brand'
SOURCE_SHA256 = 'ea8160b3667452aceac8b3939308046dcf2585c57fd4f78d98f47bf84d5547d4'
BACKGROUND = (4, 10, 23, 255)
# Pixel selection only, not a reconstructed vector logo. Interiors remain intact.
K_POLYGON = [(690,174),(945,288),(945,330),(997,333),(740,528),
             (942,716),(697,851),(424,698),(423,297)]
K_CROP = (405,155,1016,870)
PRODUCTS = {
    'logo-512.png': ('full', 512, 431, 0, False),
    'logo-1024.png': ('full', 1024, 862, 0, False),
    'logo-192.png': ('symbol', 192, 192, 8, False),
    'telegram-welcome.png': ('full', 1280, 720, 0, True),
    'description-640x360.png': ('full', 640, 360, 0, True),
    'avatar-1024.png': ('symbol', 1024, 1024, 128, True),
    'favicon-32.png': ('symbol', 32, 32, 2, False),
    'favicon-64.png': ('symbol', 64, 64, 4, False),
    'favicon-128.png': ('symbol', 128, 128, 8, False),
}


def contained(source, width, height, padding=0, flatten=False):
    resized = ImageOps.contain(source, (width-2*padding, height-2*padding),
                               Image.Resampling.LANCZOS)
    offset = ((width-resized.width)//2, (height-resized.height)//2)
    canvas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    canvas.paste(resized, offset)
    if flatten:
        background = Image.new('RGBA', canvas.size, BACKGROUND)
        background.alpha_composite(canvas)
        canvas = background.convert('RGB')
    return canvas, [*offset, resized.width, resized.height]


def extract_symbol(source):
    mask = Image.new('L', source.size, 0)
    ImageDraw.Draw(mask).polygon(K_POLYGON, fill=255)
    # An eight-pixel safety margin retains the original luminous outline.
    mask = mask.filter(ImageFilter.MaxFilter(17))
    symbol = source.copy()
    symbol.putalpha(ImageChops.multiply(source.getchannel('A'), mask))
    return symbol.crop(K_CROP)


def build():
    raw = (OUTPUT/'original.png').read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('Owner source differs; review the source before regenerating')
    source = Image.open(BytesIO(raw))
    if source.size != (1367, 1151) or source.mode != 'RGBA':
        raise ValueError('Unexpected source dimensions')
    if source.getchannel('A').getextrema()[0] != 0:
        raise ValueError('Approved transparent source required')
    symbol = extract_symbol(source)
    files = {}
    manifest = dict(source='original.png', source_sha256=SOURCE_SHA256,
                    source_size=list(source.size), source_mode='RGBA',
                    source_alpha_range=list(source.getchannel('A').getextrema()),
                    transform='full composition: contain with native alpha; K only: authorized pixel selection',
                    symbol_selection=dict(polygon=K_POLYGON, crop=K_CROP, margin=8),
                    products={})
    stream = BytesIO()
    symbol.save(stream, format='PNG', optimize=True)
    files['k-symbol.png'] = stream.getvalue()
    manifest['products']['k-symbol.png'] = dict(size=list(symbol.size), source='symbol',
                                               sha256=hashlib.sha256(files['k-symbol.png']).hexdigest())
    for name, (kind, width, height, padding, flatten) in PRODUCTS.items():
        image, bounds = contained(source if kind == 'full' else symbol, width, height, padding, flatten)
        stream = BytesIO()
        image.save(stream, format='PNG', optimize=True)
        files[name] = stream.getvalue()
        manifest['products'][name] = dict(size=[width, height], source=kind, padding=padding,
                                          flattened=flatten, source_bounds=bounds,
                                          sha256=hashlib.sha256(files[name]).hexdigest())
    icon, _ = contained(symbol, 128, 128, 8)
    stream = BytesIO()
    icon.save(stream, format='ICO', sizes=[(32, 32), (64, 64), (128, 128)])
    files['favicon.ico'] = stream.getvalue()
    manifest['products']['favicon.ico'] = dict(sizes=[[32, 32], [64, 64], [128, 128]],
                                               sha256=hashlib.sha256(files['favicon.ico']).hexdigest())
    # Telegram profile helper requires JPEG. This is a derived technical format,
    # never the canonical source and never used by website templates.
    avatar, _ = contained(symbol, 1024, 1024, 128, True)
    legacy, _ = contained(source, *source.size, flatten=True)
    for name, image in [('avatar-1024.jpg', avatar), ('legacy-logo.jpg', legacy)]:
        stream = BytesIO()
        image.save(stream, format='JPEG', quality=95, subsampling=0, optimize=True)
        files[name] = stream.getvalue()
        manifest['products'][name] = dict(size=list(image.size), flattened=True,
                                          sha256=hashlib.sha256(files[name]).hexdigest())
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
