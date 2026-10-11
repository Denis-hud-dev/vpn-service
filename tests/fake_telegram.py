"""Telegram transport double: records API methods, never connects to the network."""
from unittest.mock import AsyncMock
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage, EditMessageText, EditMessageReplyMarkup
from aiogram.types import Message, Update, InlineKeyboardMarkup
from bot.config import Config
from bot.handlers import create_router
from bot.navigation import Navigation
from bot.services import Services


class TelegramHarness:
    def __init__(self, services=None, config=None, store=None):
        self.bot = Bot('123456:offline_test_token')
        self.bot.session = AsyncMock(side_effect=self.request)
        self.calls = []
        self.messages = {}
        self.noneditable_messages = set()
        self.sequence = 100
        self.update_id = 0
        self.fail_edit = None
        self.fail_delete = None
        self.fail_answer = None
        self.navigation = Navigation(store)
        self.services = services or Services.demo()
        self.config = config or Config('123456:offline_test_token')
        self.dispatcher = Dispatcher(config=self.config, services=self.services,
                                     navigation=self.navigation, bot_username='KudaVpnBot')
        self.dispatcher.include_router(create_router())

    async def request(self, bot, method, **kwargs):
        self.calls.append(method)
        if method.__api_method__ == 'answerCallbackQuery' and self.fail_answer:
            raise TelegramBadRequest(method=method, message=self.fail_answer)
        if method.__api_method__ == 'deleteMessage' and self.fail_delete:
            raise TelegramBadRequest(method=method, message=self.fail_delete)
        if method.__api_method__ == 'deleteMessage':
            self.messages.pop((method.chat_id, method.message_id), None)
            return True
        if isinstance(method, SendMessage):
            self.sequence += 1
            message = self.bot_message(method.chat_id, self.sequence, method.text, method.reply_markup)
            self.messages[(method.chat_id, self.sequence)] = message
            if method.reply_markup is not None and not isinstance(method.reply_markup, InlineKeyboardMarkup):
                self.noneditable_messages.add((method.chat_id, self.sequence))
            return message
        if isinstance(method, (EditMessageText, EditMessageReplyMarkup)):
            if (method.chat_id, method.message_id) in self.noneditable_messages:
                raise TelegramBadRequest(method=method, message="Bad Request: message can't be edited")
            if self.fail_edit:
                if isinstance(self.fail_edit, Exception):
                    raise self.fail_edit
                raise TelegramBadRequest(method=method, message=self.fail_edit)
            key = (method.chat_id, method.message_id)
            previous = self.messages.get(key)
            if previous is None:
                raise TelegramBadRequest(method=method, message='message to edit not found')
            text = method.text if isinstance(method, EditMessageText) else previous.text
            message = self.bot_message(method.chat_id, method.message_id, text, method.reply_markup)
            self.messages[key] = message
            return message
        return True

    def bot_message(self, chat_id, message_id, text='screen', keyboard=None):
        return Message.model_validate({'message_id': message_id, 'date': 1,
            'chat': {'id': chat_id, 'type': 'private'},
            'from': {'id': self.bot.id, 'is_bot': True, 'first_name': 'KudaVPN'},
            'text': text, 'reply_markup': keyboard if keyboard and hasattr(keyboard, 'inline_keyboard') else None}).as_(self.bot)

    async def text(self, text='/start', user_id=123):
        self.update_id += 1
        raw = {'message_id': self.update_id, 'date': 1, 'chat': {'id': user_id, 'type': 'private'},
               'from': {'id': user_id, 'is_bot': False, 'first_name': 'Tester'}, 'text': text}
        if text.startswith('/start'):
            raw['entities'] = [{'type': 'bot_command', 'offset': 0, 'length': 6}]
        await self.dispatcher.feed_update(self.bot, Update.model_validate({'update_id': self.update_id, 'message': raw}))

    async def callback(self, data, user_id=123, message_id=None, inaccessible=False, inline=False):
        self.update_id += 1
        view = self.navigation.views.get(user_id)
        mid = message_id or (view.message_id if view else 101)
        message = self.messages.get((user_id, mid)) or self.bot_message(user_id, mid)
        raw = {'id': str(self.update_id), 'from': {'id': user_id, 'is_bot': False, 'first_name': 'Tester'},
               'chat_instance': 'offline', 'data': data}
        if inline:
            raw['inline_message_id'] = 'offline-inline'
        elif inaccessible:
            raw['message'] = {'message_id': mid, 'date': 0, 'chat': {'id': user_id, 'type': 'private'}}
        else:
            raw['message'] = message.model_dump(exclude_none=True)
        await self.dispatcher.feed_update(self.bot, Update.model_validate({'update_id': self.update_id, 'callback_query': raw}))

    async def close(self):
        await self.dispatcher.storage.close()
        await self.bot.session.close()
