"""Support-only screens, sharing the existing registered custom emoji IDs."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from bot.premium_emoji import EMOJI
from support_bot.knowledge import CATEGORIES, QUESTIONS
from support_bot.storage import STATUS, number


ALTERNATIVES = {'Подключение':'🔌', 'Проблемы с VPN':'⚠️', 'Серверы':'🌐',
                'Связаться с поддержкой':'💬', 'Проблема решена':'✅',
                'Нужна помощь оператора':'👨‍💻'}


def button(label, data, icon='info', custom=False):
    fallback = ALTERNATIVES.get(label, EMOJI[icon].alt)+' '+label
    return InlineKeyboardButton(text=label if custom else fallback, callback_data=data,
                                icon_custom_emoji_id=EMOJI[icon].id if custom else None)


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def plain(markup):
    if markup is None:
        return None
    icons = {entry.id:entry.alt for entry in EMOJI.values()}
    rows = []
    for row in markup.inline_keyboard:
        buttons = []
        for entry in row:
            label = entry.text
            if entry.icon_custom_emoji_id:
                label = ALTERNATIVES.get(label, icons.get(entry.icon_custom_emoji_id,'ℹ️'))+' '+label
            buttons.append(entry.model_copy(update={'text':label,'icon_custom_emoji_id':None}))
        rows.append(buttons)
    return keyboard(rows)


def controls(custom, back='home'):
    return [[button('Назад',back,'back',custom),button('Главное меню','home','home',custom)]]


def home(custom):
    rows = [[button(c.label,'cat:'+key,c.icon,custom)] for key,c in CATEGORIES.items()]
    rows += [[button('Связаться с поддержкой','contact','support',custom)],
             [button('Мои обращения','tickets','subscription',custom),button('Диагностика','diagnostics','status',custom)]]
    return ('KudaVPN Premium\n✦ Поддержка\n\nИнструкции и помощь с подключением. Выберите раздел.\n'
            'Не отправляйте пароли, коды, персональные ссылки или QR подписки.', keyboard(rows))


def category(key, custom):
    c = CATEGORIES[key]
    rows = [[button(title,'q:'+qid,'info',custom)] for qid,(cat,title,_) in QUESTIONS.items() if cat==key]
    return ('KudaVPN · '+c.label, keyboard(rows+controls(custom)))


def answer(key, custom):
    cat,title,body = QUESTIONS[key]
    return ('KudaVPN · '+title+'\n\n'+body, keyboard([
        [button('Проблема решена','solved','protect',custom)],
        [button('Нужна помощь оператора','help:'+key,'support',custom)],
        *controls(custom,'cat:'+cat)]))


def ticket_screen(ticket, custom):
    tid = ticket['id']
    text = f"{number(tid)} · {STATUS[ticket['status']]}\nКатегория: {CATEGORIES[ticket['category']].label if ticket['category'] in CATEGORIES else 'Поддержка'}"
    rows = []
    if ticket['status'] in ('new','working','waiting'):
        rows += [[button('Дополнить',f'add:{tid}','support',custom)]]
    rows += [[button('История',f'history:{tid}','info',custom)]]
    if ticket['status'] != 'closed':
        rows += [[button('Закрыть обращение',f'close:{tid}','protect',custom)]]
    return text, keyboard(rows+controls(custom,'tickets'))


def operator_keyboard(ticket, custom):
    return keyboard([
        [button('Взять в работу',f'op:take:{ticket}','support',custom)],
        [button('Запросить сведения',f'op:ask:{ticket}','info',custom)],
        [button('Решено',f'op:resolve:{ticket}','protect',custom),button('Закрыть',f'op:close:{ticket}','back',custom)]])
