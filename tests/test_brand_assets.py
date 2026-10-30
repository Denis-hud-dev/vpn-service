"""Full owner source, pixel-preserving contain layouts, and no logo replicas."""
import hashlib
import asyncio
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageOps
import pytest

from bot.branding import BRAND_DIR, BRAND_IMAGES, ORIGINAL_LOGO, WELCOME_BANNER, brand_image
from bot.handlers.start import BANNER

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HASH = 'ea8160b3667452aceac8b3939308046dcf2585c57fd4f78d98f47bf84d5547d4'


def test_one_original_matches_owner_attachment_without_reencoding():
    assert hashlib.sha256(ORIGINAL_LOGO.read_bytes()).hexdigest() == SOURCE_HASH
    source = Image.open(ORIGINAL_LOGO)
    assert source.size == (1367, 1151) and source.mode == 'RGBA'
    alpha = source.getchannel('A')
    assert alpha.getextrema() == (0, 254)
    assert alpha.histogram()[0] == 649621
    assert not (BRAND_DIR/'original.jpeg').exists()
    assert list(ROOT.rglob('kudavpn-orbit-emblem.png')) == []
    assert not (ROOT/'bot/assets/telegram-avatar-original.jpg').exists()
    assert not (ROOT/'scripts/render_welcome_banner.swift').exists()


@pytest.mark.parametrize('name', ['logo-512.png', 'logo-1024.png', 'logo-192.png', 'telegram-welcome.png',
    'description-640x360.png', 'avatar-1024.png', 'favicon-32.png', 'favicon-64.png', 'favicon-128.png'])
def test_every_png_contains_entire_source_with_proportions_and_original_colors(name):
    manifest = json.loads((BRAND_DIR/'manifest.json').read_text())
    entry = manifest['products'][name]
    raw = (BRAND_DIR/name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == entry['sha256']
    image = Image.open(BytesIO(raw))
    assert image.format == 'PNG' and image.size == tuple(entry['size'])
    x, y, width, height = entry['source_bounds']
    assert x == (image.width-width)//2 and y == (image.height-height)//2
    source = Image.open(ORIGINAL_LOGO if entry['source'] == 'full' else BRAND_DIR/'k-symbol.png').convert('RGBA')
    assert abs(width/height-source.width/source.height) < 1/height  # Only integer-pixel rounding.
    contained = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
    expected = Image.new('RGBA', image.size, (0, 0, 0, 0))
    expected.paste(contained, (x, y))
    if entry['flattened']:
        base = Image.new('RGBA', image.size, (4, 10, 23, 255))
        base.alpha_composite(expected)
        expected = base.convert('RGB')
        assert image.mode == 'RGB'
    else:
        assert image.mode == 'RGBA'
        assert image.getpixel((0,0))[3] == 0
    assert image.mode == expected.mode and image.tobytes() == expected.tobytes()


def test_telegram_and_botfather_sizes_and_single_source_are_real():
    assert BANNER == WELCOME_BANNER and BANNER.exists()
    assert Image.open(BANNER).size == (1280, 720)
    assert Image.open(BRAND_DIR/'description-640x360.png').size == (640, 360)
    config = json.loads((ROOT/'deploy/telegram-pilot/profile.json').read_text())
    assert ROOT/config['avatar'] == BRAND_DIR/'avatar-1024.jpg'
    assert Image.open(ROOT/config['avatar']).format == 'JPEG'


def test_favicon_frames_and_registry_reject_unregistered_paths():
    image = Image.open(BRAND_DIR/'favicon.ico')
    assert image.format == 'ICO'
    assert image.ico.sizes() == {(32, 32), (64, 64), (128, 128)}
    assert brand_image('kudavpn-orbit-emblem.png') == brand_image('logo.png')
    assert brand_image('kudavpn-original-logo.png')[0] == ORIGINAL_LOGO
    assert brand_image('kudavpn-original-logo.jpeg')[0] == BRAND_DIR/'legacy-logo.jpg'
    for name in BRAND_IMAGES:
        assert brand_image(name)[0].is_file()
    for name in ('../original.jpeg', 'manifest.json', 'unknown.svg'):
        with pytest.raises(KeyError): brand_image(name)


def test_compact_k_is_selected_original_pixels_without_repainting():
    manifest = json.loads((BRAND_DIR/'manifest.json').read_text())
    crop = manifest['symbol_selection']['crop']
    source = Image.open(ORIGINAL_LOGO).crop(crop)
    symbol = Image.open(BRAND_DIR/'k-symbol.png')
    assert symbol.size == source.size == (611,715)
    assert symbol.mode == 'RGBA'
    for band in 'RGB':
        assert symbol.getchannel(band).tobytes() == source.getchannel(band).tobytes()
    assert all(b == 0 or b == a for a,b in zip(source.getchannel('A').tobytes(),symbol.getchannel('A').tobytes()))
    assert symbol.getchannel('A').histogram()[0] > source.getchannel('A').histogram()[0]
    assert symbol.getpixel((300,300)) == source.getpixel((300,300))


def test_only_technical_jpegs_are_flattened_and_source_is_still_png():
    for name in ('avatar-1024.jpg','legacy-logo.jpg'):
        image = Image.open(BRAND_DIR/name)
        assert image.format == 'JPEG'
        assert max(abs(a-b) for a,b in zip(image.getpixel((0,0)),(4,10,23))) <= 3
    static = ROOT/'bot/subscription_api/static'
    for name in ('website.html','website-login.html'):
        assert '.jpeg' not in (static/name).read_text()
    icon = Image.open(BRAND_DIR/'favicon.ico').ico.getimage((32,32))
    assert icon.mode == 'RGBA' and icon.getpixel((0,0))[3] == 0


def test_templates_never_request_cropped_emblem_or_recolor_logo():
    static = ROOT/'bot/subscription_api/static'
    for p in [static/'website.html', static/'checkout.html', static/'website-login.html',
              ROOT/'bot/subscription_api/connect_page.py', *static.glob('site-*.html')]:
        text = p.read_text()
        assert 'kudavpn-orbit-emblem.png' not in text
        assert 'rel="icon"' in text
        assert 'logo.png' in text or 'logo-small.png' in text
    css = (static/'connect.css').read_text()+(static/'website.css').read_text()
    assert 'mix-blend-mode' not in css and 'object-fit:cover' not in css
    assert 'mask-image' not in css and '.orbit-motion{' not in css


@pytest.mark.parametrize('surface', ['website', 'checkout', 'subscription'])
def test_all_surfaces_serve_same_real_images_and_legacy_aliases(surface):
    from aiohttp.test_utils import TestClient, TestServer
    from bot.subscription_api.app import create_app
    from bot.subscription_api.checkout_web import create_checkout_app
    from bot.subscription_api.website import create_website

    async def run():
        if surface == 'website':
            app = create_website(None, None, None, 'https://brand.example')
            prefix = '/assets/'
        elif surface == 'checkout':
            app = create_checkout_app(None, None, 'https://brand.example')
            prefix = '/checkout/assets/'
        else:
            app = create_app(None)
            prefix = '/assets/'
        async with TestClient(TestServer(app)) as client:
            for name in BRAND_IMAGES:
                response = await client.get(prefix+name)
                path, mime = brand_image(name)
                assert response.status == 200
                assert response.headers['Content-Type'].split(';')[0] == mime
                assert await response.read() == path.read_bytes()
                assert response.headers['X-Content-Type-Options'] == 'nosniff'
                assert response.headers['Cache-Control'] == 'no-store, private'
            assert (await client.get(prefix+'unknown-brand.png')).status == 404
            if surface == 'website':
                response = await client.get('/auth/assets/logo.png')
                assert response.status == 200
                assert await response.read() == brand_image('logo.png')[0].read_bytes()
                icon = await client.get('/auth/assets/logo.png?variant=icon')
                assert icon.status == 200
                assert await icon.read() == brand_image('logo-small.png')[0].read_bytes()
                assert (await client.get('/auth/assets/logo.png?variant=../manifest.json')).status == 404
    asyncio.run(run())
