"""Offline smoke test: one premium screen, all menu routes, no polling."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiogram.methods import SendMessage, AnswerCallbackQuery, EditMessageText
from tests.fake_telegram import TelegramHarness


async def main():
    app = TelegramHarness()
    try:
        await app.text('/start ref_456')
        screen_id = app.navigation.views[123].message_id
        assert app.services.referrals.referrers[123] == 456
        routes = ['nav:connect', 'nav:happ', 'nav:home', 'nav:account', 'nav:devices',
                  'nav:tariffs', 'tariff:month', 'tariff:quarter', 'tariff:year',
                  'nav:traffic', 'nav:bonuses', 'nav:support', 'obsolete', 'tariff:invalid', 'nav:home']
        for route in routes:
            await app.callback(route)
        await app.text('/start')
        assert sum(isinstance(call, SendMessage) for call in app.calls) == 1
        assert sum(isinstance(call, AnswerCallbackQuery) for call in app.calls) == len(routes)
        assert all(call.message_id == screen_id for call in app.calls if isinstance(call, EditMessageText))
        print('PASS: premium menu, tariffs, account, Happ, traffic, bonuses, support; one screen; all callbacks answered')
    finally:
        await app.close()


if __name__ == '__main__':
    asyncio.run(main())
