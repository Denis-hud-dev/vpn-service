"""Low-cardinality timings: never record update payloads, SQL or identifiers."""
import logging
from time import perf_counter
from aiogram import BaseMiddleware
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.types import CallbackQuery, Message, ReplyKeyboardMarkup
from sqlalchemy import event

logger = logging.getLogger(__name__)


def timing(operation: str, started: float, outcome: str) -> None:
    elapsed = (perf_counter() - started) * 1000
    logger.log(logging.WARNING if elapsed >= 1000 else logging.INFO,
               'timing operation=%s duration_ms=%.1f outcome=%s', operation, elapsed, outcome)


class UpdateTimingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Only fixed categories; no command args, callback payload, names or IDs.
        operation = 'callback' if isinstance(event, CallbackQuery) else 'message'
        if isinstance(event, Message) and (event.text or '').split(maxsplit=1)[0:1] == ['/start']:
            operation = 'start'
        started = perf_counter()
        outcome = 'ok'
        try:
            return await handler(event, data)
        except BaseException:
            outcome = 'interrupted'
            raise
        finally:
            timing(operation, started, outcome)


class TelegramTimingMiddleware(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        # Method names are library constants; skip idle long polling.
        if method.__api_method__ == 'getUpdates':
            try:
                return await make_request(bot, method)
            except Exception as exc:
                kind = type(exc).__name__
                allowed = {'TelegramConflictError', 'TelegramNetworkError',
                           'TelegramUnauthorizedError', 'TelegramRetryAfter',
                           'TelegramServerError', 'TelegramBadRequest', 'TimeoutError'}
                logger.warning('polling_failure kind=%s', kind if kind in allowed else 'other')
                raise
        started = perf_counter()
        outcome = 'ok'
        keyboard = getattr(method, 'reply_markup', None)
        pilot_keyboard = isinstance(keyboard, ReplyKeyboardMarkup)
        if pilot_keyboard:
            rows = [[button.text for button in row] for row in keyboard.keyboard]
            logger.info('reply_keyboard_request layout_ok=%s persistent=%s',
                        rows == [['🔑 Подключиться'], ['🏠 Личный кабинет', 'ℹ️ Информация']],
                        keyboard.is_persistent is True)
        try:
            result = await make_request(bot, method)
            if pilot_keyboard:
                # Telegram acknowledges the sent Message; reply keyboards are not
                # echoed in Message.reply_markup (reserved for inline keyboards).
                logger.info('reply_keyboard_ack message_received=%s', isinstance(result, Message))
            return result
        except BaseException:
            outcome = 'interrupted'
            raise
        finally:
            timing('telegram.' + method.__api_method__, started, outcome)


def instrument_database(engine) -> None:
    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def before(conn, cursor, statement, parameters, context, executemany):
        context._kudavpn_started = perf_counter()

    @event.listens_for(engine.sync_engine, 'after_cursor_execute')
    def after(conn, cursor, statement, parameters, context, executemany):
        timing('database.query', context._kudavpn_started, 'ok')

    @event.listens_for(engine.sync_engine, 'handle_error')
    def error(context):
        started = getattr(context.execution_context, '_kudavpn_started', None)
        if started is not None:
            timing('database.query', started, 'interrupted')


class SafeDependencyLogs(logging.Filter):
    """Third-party exceptions can embed Telegram URLs, updates and DB credentials."""
    def filter(self, record):
        if record.name.startswith(('aiogram', 'aiohttp', 'sqlalchemy', 'psycopg')):
            if record.levelno < logging.WARNING:
                return False
            record.msg = 'Dependency warning/error; inspect timing and service readiness'
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return True
