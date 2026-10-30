"""Full owner source, pixel-preserving contain layouts, and no logo replicas."""
import hashlib
import asyncio
from io import BytesIO
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageOps
import pytest

from bot.branding import BRAND_DIR, BRAND_IMAGES, ORIGINAL_LOGO, WELCOME_BANNER, brand_image
from bot.handlers.start import BANNER

ROOT = Path(__file__).resolve().parents[1]
SOURCE_HASH = '084ba43d6f5cd0ae8e51c6896db141dd9c78f0d80807917ef8917becaeda2337'


def test_one_original_matches_owner_attachment_without_reencoding():
    assert hashlib.sha256(ORIGINAL_LOGO.read_bytes()).hexdigest() == SOURCE_HASH
    assert Image.open(ORIGINAL_LOGO).size == (1254, 1254)
    assert list(ROOT.rglob('kudavpn-orbit-emblem.png')) == []
    assert not (ROOT/'bot/assets/telegram-avatar-original.jpg').exists()
    assert not (ROOT/'scripts/render_welcome_banner.swift').exists()


@pytest.mark.parametrize('name', ['logo-512.png', 'logo-192.png', 'telegram-welcome.png',
    'description-640x360.png', 'avatar-1024.png', 'favicon-32.png', 'favicon-64.png', 'favicon-128.png'])
def test_every_png_contains_entire_source_with_proportions_and_original_colors(name):
    manifest = json.loads((BRAND_DIR/'manifest.json').read_text())
    entry = manifest['products'][name]
    raw = (BRAND_DIR/name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == entry['sha256']
    image = Image.open(BytesIO(raw))
    assert image.format == 'PNG' and image.size == tuple(entry['size'])
    x, y, width, height = entry['source_bounds']
    assert width == height  # The square owner artwork is never stretched.
    assert x == (image.width-width)//2 and y == (image.height-height)//2
    source = Image.open(ORIGINAL_LOGO).convert('RGB')
    contained = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
    expected = Image.new('RGB', image.size, (0, 0, 0))
    expected.paste(contained, (x, y))
    assert ImageChops.difference(image.convert('RGB'), expected).getbbox() is None


def test_telegram_and_botfather_sizes_and_single_source_are_real():
    assert BANNER == WELCOME_BANNER and BANNER.exists()
    assert Image.open(BANNER).size == (1280, 720)
    assert Image.open(BRAND_DIR/'description-640x360.png').size == (640, 360)
    config = json.loads((ROOT/'deploy/telegram-pilot/profile.json').read_text())
    assert ROOT/config['avatar'] == ORIGINAL_LOGO


def test_favicon_frames_and_registry_reject_unregistered_paths():
    image = Image.open(BRAND_DIR/'favicon.ico')
    assert image.format == 'ICO'
    assert image.ico.sizes() == {(32, 32), (64, 64), (128, 128)}
    assert brand_image('kudavpn-orbit-emblem.png') == brand_image('logo.png')
    assert brand_image('kudavpn-original-logo.jpeg')[0] == ORIGINAL_LOGO
    for name in BRAND_IMAGES:
        assert brand_image(name)[0].is_file()
    for name in ('../original.jpeg', 'manifest.json', 'unknown.svg'):
        with pytest.raises(KeyError): brand_image(name)


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
    asyncio.run(run())
