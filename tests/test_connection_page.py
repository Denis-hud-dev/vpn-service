"""The connection page inherits feed entitlement and keeps bearer data private."""
import asyncio

from aiohttp.test_utils import TestClient, TestServer

from bot.config import Config
from bot.screens import connect
from bot.services.happ import AccessDenied, HappService
from bot.subscription_api.app import create_app

TOKEN = 'a' * 43
ORIGIN = 'https://subscriptions.example'


class Feed:
    public_url = ORIGIN
    enabled = True

    async def feed(self, token):
        if not self.enabled or token != TOKEN:
            raise AccessDenied('unavailable')
        return 'vless://private-credential@example.invalid:10443\n', 1790000000


def test_page_reuses_feed_entitlement_and_keeps_private_headers():
    async def run():
        feed = Feed()
        client = TestClient(TestServer(create_app(feed)))
        await client.start_server()
        try:
            response = await client.get('/connect/' + TOKEN)
            body = await response.text()
            assert response.status == 200
            assert 'text/html' in response.headers['Content-Type']
            assert response.headers['Cache-Control'] == 'no-store, private'
            assert response.headers['Referrer-Policy'] == 'no-referrer'
            assert "default-src 'none'" in response.headers['Content-Security-Policy']
            assert response.headers['X-Frame-Options'] == 'DENY'
            assert 'KudaVPN Premium' in body and 'Android' in body and 'macOS' in body
            assert ORIGIN + '/s/' + TOKEN in body
            assert '<svg' in body  # QR is generated locally, without a third-party endpoint.
            assert 'vless://private-credential' not in body
            assert 'ДЕЙСТВУЕТ ДО' in body
            assert 'https://subscriptions.example/s/' in body
            assert 'id="import-steps"' in body
            assert 'официального сайта Happ' in body
            assert 'rel="noreferrer noopener"' in body
            assert 'happ://' not in body  # No unverified subscription deep link.
            assert body.count('src="/assets/logo.png"') == 1
            assert body.count('/assets/logo-small.png') == 1
            assert '/assets/kudavpn-orbit-emblem.png' not in body
            assert 'orbit-ring' not in body
            assert 'class="hero-connect" href="#choose-platform"' in body
            assert 'id="choose-platform"' in body
            assert 'class="orbit-motion"' not in body
            assert '<animateMotion' not in body  # No painted-over replacement orbit.
            for name in ('connect.css', 'connect.js'):
                asset = await client.get('/assets/' + name)
                assert asset.status == 200 and (await asset.read())
                assert asset.headers['Cache-Control'] == 'no-store, private'
                assert TOKEN not in (await asset.text())
                if name == 'connect.css':
                    css = await asset.text()
                    assert '.hero-connect:focus-visible' in css
                    assert 'prefers-reduced-motion:reduce' in css
                    assert 'mix-blend-mode' not in css
            logo = await client.get('/assets/kudavpn-original-logo.jpeg')
            assert logo.status == 200
            assert logo.headers['Content-Type'].startswith('image/jpeg')
            assert (await logo.read()).startswith(b'\xff\xd8\xff')
            assert logo.headers['Cache-Control'] == 'no-store, private'
            emblem = await client.get('/assets/kudavpn-orbit-emblem.png')
            assert emblem.status == 200
            assert emblem.headers['Content-Type'].startswith('image/png')
            assert (await emblem.read()).startswith(b'\x89PNG\r\n\x1a\n')
            current = await client.get('/assets/logo.png')
            assert (await current.read()) == (await emblem.read())  # Safe legacy URL alias.
            assert (await client.get('/assets/favicon.ico')).status == 200
            assert (await client.get('/assets/unknown.js')).status == 404
            response = await client.get('/s/' + TOKEN)
            assert response.status == 200 and 'vless://' in await response.text()
            feed.enabled = False
            response = await client.get('/connect/' + TOKEN)
            assert response.status == 404
            assert TOKEN not in await response.text()
            assert (await client.get('/connect/' + 'b' * 43)).status == 404
            # Unknown routes also pass through private headers without the
            # deprecated aiohttp HTTPException-as-response behaviour.
            unknown = await client.get('/missing')
            assert unknown.status == 404
            assert unknown.headers['Cache-Control'] == 'no-store, private'
        finally:
            await client.close()
    asyncio.run(run())


def test_pilot_button_is_opt_in_and_legacy_link_remains():
    link = ORIGIN + '/s/' + TOKEN
    page = ORIGIN + '/connect/' + TOKEN
    default = connect(link)
    assert [b.url for row in default.keyboard.inline_keyboard for b in row if b.url] == [link]
    enabled = connect(link, page_url=page)
    assert [b.url for row in enabled.keyboard.inline_keyboard for b in row if b.url] == [page, link]
    assert link in enabled.text
    assert 'PILOT_CONNECT_PAGE_ENABLED' not in enabled.text


def test_page_url_only_uses_issued_origin_and_pilot_gate(monkeypatch):
    service = HappService(None, ORIGIN)
    assert service.connection_page_url(ORIGIN + '/s/' + TOKEN) == ORIGIN + '/connect/' + TOKEN
    for bad in ('https://other.example/s/' + TOKEN, ORIGIN + '/s/invalid', ORIGIN + '/s/' + TOKEN + '?x=1'):
        try:
            service.connection_page_url(bad)
        except AccessDenied:
            pass
        else:
            assert False, 'invalid link accepted'
    monkeypatch.setenv('BOT_TOKEN', 'test-placeholder')
    monkeypatch.setenv('KUDAVPN_PILOT_MARKER', 'kudavpn-telegram-pilot-v1')
    monkeypatch.setenv('PILOT_TRIAL_OWNER_ID', '123')
    monkeypatch.setenv('PILOT_CONNECT_PAGE_ENABLED', '1')
    assert Config.from_env().pilot_connect_page_enabled
    monkeypatch.delenv('KUDAVPN_PILOT_MARKER')
    assert not Config.from_env().pilot_connect_page_enabled
