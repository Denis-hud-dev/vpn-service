"""Private-user support and strictly authorized operator-group routing."""
import asyncio
import json
import logging
import re
import time
from aiogram import Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import CallbackQuery, Message, LinkPreviewOptions

from support_bot import ui
from support_bot.diagnostics import Diagnostics
from support_bot.knowledge import CATEGORIES, QUESTIONS
from support_bot.storage import Rejected, STATUS, number

log = logging.getLogger(__name__)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


async def dbcall(function, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


async def send_text(bot, chat, text, markup=None):
    try:
        return await bot.send_message(chat, text, reply_markup=markup, parse_mode=None,
                                      protect_content=True, link_preview_options=NO_PREVIEW)
    except TelegramBadRequest as error:
        # Retry only an explicit emoji validation failure, never a network timeout.
        if markup and 'emoji' in error.message.casefold() and any(
                b.icon_custom_emoji_id for row in markup.inline_keyboard for b in row):
            return await bot.send_message(chat, text, reply_markup=ui.plain(markup), parse_mode=None,
                                          protect_content=True, link_preview_options=NO_PREVIEW)
        raise


def content(message):
    text = (message.text or message.caption or '').strip()
    attachment = None
    if message.photo:
        attachment = {'kind':'photo','file_id':message.photo[-1].file_id}
        size = message.photo[-1].file_size or 0
    elif message.document:
        if message.document.mime_type not in {'image/png','image/jpeg','image/webp'}:
            raise Rejected('image_required')
        attachment = {'kind':'document','file_id':message.document.file_id}
        size = message.document.file_size or 0
    elif any((message.video,message.audio,message.voice,message.sticker,message.animation,message.contact,message.location)):
        raise Rejected('image_required')
    else:
        size = 0
    if size > 10*1024**2 or len(text) > (700 if attachment else 3000):
        raise Rejected('message_too_large')
    if not text and not attachment:
        raise Rejected('empty_message')
    # Don't store/rebroadcast recognizable credentials and personal connection links.
    if (re.search(r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b|(?:vless|vmess|ss|happ)://', text, re.I)
            or re.search(r'https?://\S*(?:/sub/|/subscription/|[?&](?:token|secret|key)=)',text,re.I)):
        raise Rejected('sensitive_content')
    return text or 'Скриншот', attachment


ERRORS = {
    'rate_limited':'Слишком много сообщений. Подождите минуту; обращение и история сохранены.',
    'ticket_not_found':'Обращение не найдено или недоступно этому аккаунту.',
    'ticket_closed':'Это обращение завершено. Создайте новое через меню поддержки.',
    'ticket_unavailable':'Обращение завершено или уже взято другим оператором.',
    'image_required':'Поддерживаются текст и скриншоты PNG/JPEG/WEBP.',
    'message_too_large':'Сократите текст до 3000 символов (со скриншотом — до 700). Размер изображения — до 10 МБ.',
    'empty_message':'Напишите описание проблемы или отправьте скриншот.',
    'sensitive_content':'Не отправляйте токены и персональные ссылки. Опишите ошибку без секретов.',
}


class Application:
    def __init__(self, store, config, diagnostics=None):
        self.store, self.config = store, config
        self.diagnostics = diagnostics or Diagnostics()
        self.router = Router(name='kudavpn_support')
        self.router.message.register(self.message)
        self.router.callback_query.register(self.callback)

    def operator(self, event):
        return (event.chat.id == self.config.admin_chat_id and event.chat.type in {'group','supergroup'}
                and event.from_user is not None and not event.from_user.is_bot
                and event.from_user.id in self.config.operator_ids and event.sender_chat is None
                and event.forward_origin is None and event.via_bot is None
                and event.external_reply is None and event.is_automatic_forward is not True)

    def private(self, message, user):
        return (message.chat.type == 'private' and user is not None and not user.is_bot
                and message.chat.id == user.id and user.id > 0
                and (self.config.test_user_ids is None or user.id in self.config.test_user_ids))

    async def navigate(self, bot, owner, key, text, markup):
        state = await dbcall(self.store.session,owner)
        sent = await send_text(bot,owner,text,markup)
        await dbcall(self.store.set_session,owner,nav_id=sent.message_id,nav_key=key)
        # Only a persisted navigation message in this exact private chat is eligible.
        if state['nav_id'] and state['nav_id'] != sent.message_id:
            try:
                await bot.delete_message(owner,state['nav_id'])
            except TelegramAPIError:
                try:
                    await bot.edit_message_reply_markup(chat_id=owner,message_id=state['nav_id'],reply_markup=None)
                except TelegramAPIError:
                    log.warning('support_navigation_cleanup_unavailable')

    async def screen(self, bot, owner, key):
        custom = self.config.custom_emoji
        if key == 'home':
            await dbcall(self.store.set_session,owner,category=None,question=None,target=None)
            text, markup = ui.home(custom)
        elif key.startswith('cat:') and key[4:] in CATEGORIES:
            await dbcall(self.store.set_session,owner,category=None,question=None,target=None)
            text, markup = ui.category(key[4:],custom)
        elif key.startswith('q:') and key[2:] in QUESTIONS:
            text, markup = ui.answer(key[2:],custom)
        elif key == 'solved':
            text = 'Рады, что всё получилось. При необходимости поддержка остаётся доступной.'
            markup = ui.keyboard(ui.controls(custom))
        elif key == 'contact' or (key.startswith('help:') and key[5:] in QUESTIONS):
            question = key[5:] if key.startswith('help:') else None
            category = QUESTIONS[question][0] if question else 'contact'
            await dbcall(self.store.set_session,owner,category=category,question=question,target=None)
            text = ('Обращение в KudaVPN\n\nОпишите проблему одним сообщением или отправьте скриншот. '
                    'Укажите устройство, ОС, версию Happ и тип сети.\n'
                    'Не отправляйте ключи, персональную ссылку, QR подписки и банковские данные. '
                    'Скройте их на скриншоте. Сообщение будет передано закрытой группе поддержки.')
            markup = ui.keyboard(ui.controls(custom))
        elif key == 'tickets':
            await dbcall(self.store.set_session,owner,category=None,question=None,target=None)
            tickets = await dbcall(self.store.list_tickets,owner)
            text = 'Мои обращения\n\n'+('Выберите обращение.' if tickets else 'Обращений пока нет.')
            markup = ui.keyboard([[ui.button(number(t['id'])+' · '+STATUS[t['status']],f'ticket:{t["id"]}','support',custom)]
                                  for t in tickets]+ui.controls(custom))
        elif key == 'diagnostics':
            text = await self.diagnostics.report(owner)
            markup = ui.keyboard(ui.controls(custom))
        elif re.fullmatch(r'(ticket|add|close|history):[1-9]\d{0,9}',key):
            action, raw = key.split(':');tid=int(raw)
            target = await dbcall(self.store.get_ticket,tid,owner)
            if action == 'add':
                if target['status'] not in ('new','working','waiting'):
                    raise Rejected('ticket_closed')
                await dbcall(self.store.set_session,owner,target=tid,category=None,question=None)
                text = number(tid)+' · Отправьте дополнение или скриншот без секретов.'
                markup = ui.keyboard(ui.controls(custom,'ticket:'+raw))
            elif action == 'history':
                history = await dbcall(self.store.transcript,tid,owner)
                text = number(tid)+' · Последние записи\n\n'+'\n\n'.join(
                    ('Вы' if r['actor']==owner else 'Поддержка')+': '+r['text'][:280]+(' [скриншот]' if r['attachment'] else '')
                    for r in history)
                markup = ui.keyboard(ui.controls(custom,'ticket:'+raw))
            elif action == 'close':
                # Closing is handled by callback with its unique event source.
                raise Rejected('invalid_action')
            else:
                text, markup = ui.ticket_screen(target,custom)
        else:
            text, markup = ui.home(custom)
        await self.navigate(bot,owner,key,text,markup)

    async def message(self, message: Message, bot):
        try:
            if self.operator(message):
                reply = message.reply_to_message
                if (not reply or reply.chat.id != self.config.admin_chat_id or not reply.from_user
                        or reply.from_user.id != bot.id or not reply.from_user.is_bot
                        or reply.forward_origin is not None):
                    return
                ticket = await dbcall(self.store.bound_ticket,message.chat.id,reply.message_id)
                if ticket is None:
                    return
                text, attachment = content(message)
                await dbcall(self.store.operator_action,ticket,message.from_user.id,'reply',
                             f'tg:{message.chat.id}:{message.message_id}',text,attachment)
                return
            if not self.private(message,message.from_user):
                return
            owner = message.from_user.id
            command = (message.text or '').split(maxsplit=1)[0:1]
            if command in (['/start'],['/help'],['/menu']):
                await self.screen(bot,owner,'home')
                return
            if command == ['/tickets']:
                await self.screen(bot,owner,'tickets')
                return
            state = await dbcall(self.store.session,owner)
            if not state['category'] and not state['target']:
                await self.navigate(bot,owner,'contact_hint','Чтобы обратиться к оператору, выберите «Связаться с поддержкой».',
                                ui.keyboard([[ui.button('Связаться с поддержкой','contact','support',self.config.custom_emoji)]]))
                return
            text, attachment = content(message)
            await dbcall(self.store.user_message,owner,state['category'] or 'contact',state['question'],
                         text,attachment,f'tg:{message.chat.id}:{message.message_id}')
        except Rejected as error:
            if self.private(message,message.from_user) or self.operator(message):
                try:
                    await send_text(bot,message.chat.id,ERRORS.get(str(error),'Действие недоступно. Откройте главное меню.'))
                except TelegramAPIError:
                    log.warning('support_rejection_notice_unavailable')
        except TelegramAPIError:
            log.warning('support_interactive_delivery_failed')

    async def callback(self, query: CallbackQuery, bot):
        message = query.message
        if not isinstance(message,Message):
            return
        try:
            key = query.data or ''
            if key.startswith('op:'):
                # The message's author is the bot; the actor comes from CallbackQuery.
                if (message.chat.id != self.config.admin_chat_id or message.chat.type not in {'group','supergroup'}
                        or query.from_user.id not in self.config.operator_ids or query.from_user.is_bot
                        or not message.from_user or message.from_user.id != bot.id
                        or message.forward_origin is not None or message.sender_chat is not None
                        or message.via_bot is not None):
                    raise Rejected('operator_only')
                match = re.fullmatch(r'op:(take|ask|resolve|close):([1-9]\d{0,9})',key)
                ticket = await dbcall(self.store.bound_ticket,message.chat.id,message.message_id)
                if not match or ticket != int(match[2]):
                    raise Rejected('ticket_not_found')
                await dbcall(self.store.operator_action,ticket,query.from_user.id,match[1],'cb:'+query.id)
                await query.answer('Сохранено. Для ответа используйте Reply на сообщение обращения.')
            elif self.private(message,query.from_user):
                if not message.from_user or message.from_user.id != bot.id:
                    return
                if re.fullmatch(r'close:[1-9]\d{0,9}',key):
                    tid = int(key.split(':')[1])
                    await dbcall(self.store.close_user,tid,query.from_user.id,'cb:'+query.id)
                    key = 'ticket:'+str(tid)
                await query.answer()
                await self.screen(bot,query.from_user.id,key)
            else:
                await query.answer('Недоступно',show_alert=True)
        except Rejected as error:
            try:
                await query.answer(ERRORS.get(str(error),'Недостаточно прав или действие недоступно.'),show_alert=True)
            except TelegramAPIError:
                pass
        except TelegramAPIError:
            log.warning('support_callback_delivery_failed')


async def dispatch_one(store, config, bot):
    delivery = await dbcall(store.claim)
    if delivery is None:
        return False
    payload = json.loads(delivery['payload'])
    markup = (ui.operator_keyboard(delivery['ticket'],config.custom_emoji)
              if delivery['audience']=='operator' else ui.keyboard([
                  [ui.button('Открыть обращение',f"ticket:{delivery['ticket']}",'support',config.custom_emoji)],
                  [ui.button('Главное меню','home','home',config.custom_emoji)]]))
    try:
        attachment = payload['attachment']
        if attachment:
            method = bot.send_photo if attachment['kind']=='photo' else bot.send_document
            args = {'photo' if attachment['kind']=='photo' else 'document':attachment['file_id']}
            try:
                sent = await method(delivery['chat'],**args,caption=payload['text'],reply_markup=markup,
                                    parse_mode=None,protect_content=True)
            except TelegramBadRequest as error:
                if markup and config.custom_emoji and 'emoji' in error.message.casefold():
                    sent = await method(delivery['chat'],**args,caption=payload['text'],reply_markup=ui.plain(markup),
                                        parse_mode=None,protect_content=True)
                else:
                    raise
        else:
            sent = await send_text(bot,delivery['chat'],payload['text'],markup)
        if sent.chat.id != delivery['chat'] or sent.message_id <= 0:
            raise RuntimeError('delivery_ack_mismatch')
        await dbcall(store.sent,delivery,sent.message_id)
    except TelegramRetryAfter as error:
        await dbcall(store.failed,delivery,'rate_limit',delay=error.retry_after)
    except TelegramForbiddenError:
        await dbcall(store.failed,delivery,'forbidden',permanent=True)
        log.error('support_outbox_blocked reason=forbidden')
    except TelegramBadRequest:
        await dbcall(store.failed,delivery,'invalid_request',permanent=True)
        log.error('support_outbox_blocked reason=invalid_request')
    except Exception:
        # No payload, user/operator identity, file_id, token or exception string in logs.
        await dbcall(store.failed,delivery,'transport_or_ack_failed')
        log.warning('support_outbox_retry')
    return True


async def delivery_worker(store,config,bot):
    last_warning = 0
    while True:
        processed = await dispatch_one(store,config,bot)
        health = await dbcall(store.health)
        if health['blocked'] and time.monotonic()-last_warning >= 60:
            last_warning = time.monotonic()
            log.error('support_delivery_attention blocked=%d pending=%d',health['blocked'],health['pending'])
        await asyncio.sleep(0.2 if processed else 2)
