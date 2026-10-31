"""Only disposable local fixtures; never connect to VPS, OAuth or payment APIs."""
import asyncio
from html.parser import HTMLParser
import sqlite3

from aiohttp.test_utils import TestClient, TestServer
import pytest

from bot.branding import brand_image
from scripts.preview_website import build


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []
        self.hero = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'img', 'script'} and attrs.get('src'):
            self.urls.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') in {'stylesheet', 'icon'}:
            self.urls.append(attrs['href'])
        if tag == 'img' and 'orbit-emblem' in attrs.get('class', ''):
            self.hero.append(attrs)


@pytest.mark.parametrize('route', ['/', '/__demo__/connect', '/__demo__/login-screen'])
def test_local_pages_load_approved_images_styles_and_scripts_without_a_purchase(tmp_path, route):
    async def run():
        app = await build(18118, tmp_path)
        async with TestClient(TestServer(app)) as client:
            response = await client.get(route)
            assert response.status == 200
            body = await response.text()
            resources = Resources()
            resources.feed(body)
            assert resources.urls
            for url in resources.urls:
                asset = await client.get(url)
                assert asset.status == 200 and await asset.read()
                assert asset.headers['X-Content-Type-Options'] == 'nosniff'
            if route == '/__demo__/connect':
                assert 'ВЫМЫШЛЕННЫЕ ДАННЫЕ' in body
                assert 'НЕ ИМПОРТИРУЙТЕ В HAPP' in body
                assert 'http://127.0.0.1:18118/__demo__/feed' in body
                assert '<svg' in body
                assert len(resources.hero) == 1
                hero = resources.hero[0]
                assert hero['src'] == '/assets/logo.png'
                assert hero['width'] == '512' and hero['height'] == '431'
                assert 'kudavpn-original-logo.png 1367w' in hero['srcset']
                assert await (await client.get(hero['src'])).read() == brand_image('logo.png')[0].read_bytes()
            if route == '/':
                assert 'href="/__demo__/connect"' in body
                assert 'href="/__demo__/login-screen"' in body
        # Visual inspection does not issue a payment or a subscription.
        with sqlite3.connect(tmp_path/'demo.sqlite') as connection:
            for table in ('payments', 'subscriptions', 'subscription_tokens'):
                assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
    asyncio.run(run())


def test_static_fixture_preview_does_not_authorize_personal_pages(tmp_path):
    async def run():
        async with TestClient(TestServer(await build(18118, tmp_path))) as client:
            assert (await client.get('/__demo__/connect')).status == 200
            for route in ('/connect', '/api/account', '/__demo__/feed'):
                response = await client.get(route)
                assert response.status == 401
            response = await client.get('/api/plans')
            plans = (await response.json())['plans']
            assert [p['amount_kopecks'] for p in plans] == [19900, 49900, 179000]
    asyncio.run(run())
