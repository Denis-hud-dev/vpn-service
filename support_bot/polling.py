"""Acknowledge Telegram updates only after successful durable handling.

Dispatcher.start_polling internally logs/consumes handler exceptions. Support
uses feed_update directly so a SQLite failure cannot silently acknowledge an
unsaved ticket. No webhook deletion or remote configuration is performed.
"""
import asyncio
import logging
from aiogram.exceptions import TelegramNetworkError, TelegramServerError, TelegramRetryAfter
from support_bot.application import dbcall

log = logging.getLogger(__name__)


async def poll(bot,dispatcher,store):
    offset = await dbcall(store.offset)
    while True:
        try:
            updates = await bot.get_updates(offset=offset,timeout=20,request_timeout=30,
                                           allowed_updates=['message','callback_query'])
        except TelegramRetryAfter as error:
            log.warning('support_polling_backoff')
            await asyncio.sleep(max(1,error.retry_after))
            continue
        except (TelegramNetworkError,TelegramServerError):
            log.warning('support_polling_unavailable')
            await asyncio.sleep(5)
            continue
        for update in updates:
            await dispatcher.feed_update(bot,update)
            # A crash between handling and saving offset can replay an update;
            # unique history sources make ticket/reply processing idempotent.
            offset = update.update_id+1
            await dbcall(store.save_offset,offset)
