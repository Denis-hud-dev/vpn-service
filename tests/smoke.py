"""Offline integration smoke test with real Dispatcher routing."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandObject
from aiogram.types import Update
from bot.config import Config
from bot.handlers import create_router
from bot.keyboards.main import CONNECT, BUY, SUBSCRIPTION, DEVICES, REFERRAL, INSTRUCTIONS, SUPPORT
from bot.services import Services, parse_referrer


async def main():
    services = Services.demo()
    config = Config("123456:offline_test_token")
    bot = Bot(config.bot_token)
    bot.session = AsyncMock()
    dp = Dispatcher(config=config, services=services, bot_username="KudaVpnBot")
    dp.include_router(create_router())
    user = {"id": 123, "is_bot": False, "first_name": "Tester"}
    chat = {"id": 123, "type": "private"}
    counter = 0

    async def send(text=None, callback=None):
        nonlocal counter
        counter += 1
        message = {"message_id": counter, "date": 1, "chat": chat, "from": user, "text": text or "screen"}
        if text and text.startswith("/start"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": 6}]
        raw = {"update_id": counter, "message": message}
        if callback:
            raw = {"update_id": counter, "callback_query": {"id": str(counter), "from": user, "chat_instance": "offline", "data": callback, "message": message}}
        bot.session.reset_mock()
        await dp.feed_update(bot, Update.model_validate(raw))
        methods = [call.args[1] for call in bot.session.call_args_list]
        assert methods, (text, callback)
        return methods

    for text in ["/start ref_456", CONNECT, BUY, SUBSCRIPTION, DEVICES, REFERRAL, INSTRUCTIONS, SUPPORT, "unknown"]:
        methods = await send(text=text)
        assert any(getattr(method, "text", None) for method in methods)
    assert services.referrals.referrers[123] == 456
    methods = await send(text=REFERRAL)
    assert "https://t.me/KudaVpnBot?start=ref_123" in methods[-1].text
    for callback in ["buy", "tariff:month", "tariff:quarter", "tariff:year", "device:add", "instructions", "instruction:ios", "instruction:android", "instruction:windows", "instruction:macos", "menu", "obsolete", "tariff:invalid"]:
        await send(callback=callback)
    config = Config(config.bot_token, "kudavpn_support")
    dp["config"] = config
    methods = await send(text=SUPPORT)
    assert methods[-1].reply_markup.inline_keyboard[0][0].url == "https://t.me/kudavpn_support"
    await services.referrals.register(123, 789)
    await services.referrals.register(789, 789)
    assert services.referrals.referrers == {123: 456, 789: None}
    for payload in [None, "ref_0", "ref_-1", "ref_abc", "ref_１２", "ref_" + "9" * 21]:
        assert parse_referrer(payload) is None
    await dp.storage.close()
    await bot.session.close()
    print("PASS: all menu routes, tariffs, platforms, back, support, fallback and referral attribution")


if __name__ == "__main__":
    asyncio.run(main())
