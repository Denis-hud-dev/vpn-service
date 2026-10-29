import asyncio
from aiogram.methods import SendMessage,SendPhoto
from aiogram.types import ReplyKeyboardMarkup
from bot import screens
from bot.premium_emoji import EMOJI,decorate
from bot.services.account import AccountSnapshot
from tests.fake_telegram import TelegramHarness
from tests.test_start_premium_menu import pilot


def test_first_banner_carries_keyboard_and_buy_is_always_first():
 async def run():
  app=TelegramHarness(services=pilot(AccountSnapshot()));app.navigation.custom_emoji=True
  try:
   await app.text('/start')
   photos=[c for c in app.calls if isinstance(c,SendPhoto)];assert len(photos)==1
   assert isinstance(photos[0].reply_markup,ReplyKeyboardMarkup)
   assert photos[0].reply_markup.keyboard[0][0].icon_custom_emoji_id==EMOJI['premium'].id
   assert '1.' in photos[0].caption and '2.' in photos[0].caption and '3.' in photos[0].caption
   assert not any(isinstance(c,SendMessage) and isinstance(c.reply_markup,ReplyKeyboardMarkup) for c in app.calls)
   await app.text('Купить VPN');assert all(p in app.navigation.views[123].screen.text for p in ('199','499','1790'))
   app.services.test_checkout.create_for_user.assert_not_awaited()
   await app.text('/start');await app.text('/start')
   assert len([c for c in app.calls if isinstance(c,SendPhoto)])==1
   assert not any('Навигация KudaVPN' in (c.text or '') for c in app.calls if isinstance(c,SendMessage))
   greetings=[c for c in app.calls if isinstance(c,SendMessage) and isinstance(c.reply_markup,ReplyKeyboardMarkup)]
   assert len(greetings)==2 and 'Подписка:' in greetings[-1].text
   assert not any('1.' in c.text for c in greetings)
   assert len([m for m in app.messages.values() if m.reply_markup])<=2
  finally:await app.close()
 asyncio.run(run())


def test_information_instruction_and_nested_buttons_have_real_brand_ids():
 info=decorate(screens.information(''))
 instruction=next(b for row in info.keyboard.inline_keyboard for b in row if b.callback_data=='nav:happ')
 assert instruction.text=='Инструкция' and instruction.icon_custom_emoji_id==EMOJI['connect'].id
 for screen in [screens.account_management(),screens.kuda_smart(),screens.help_center(''),screens.happ()]:
  buttons=[b for row in decorate(screen).keyboard.inline_keyboard for b in row]
  assert all(b.icon_custom_emoji_id for b in buttons if b.text in ('Назад','Главное меню','Инструкция'))


def test_short_actions_screen_is_safely_adopted_if_navigation_pointer_is_missing():
 async def run():
  app=TelegramHarness(services=pilot(AccountSnapshot()))
  try:
   await app.text('/start');mid=app.navigation.views[123].message_id
   app.navigation.views.clear();app.navigation.store.screens.clear()
   await app.callback('nav:tariffs',message_id=mid)
   assert '199' in app.navigation.views[123].screen.text
   assert app.navigation.views[123].message_id==mid
  finally:await app.close()
 asyncio.run(run())
