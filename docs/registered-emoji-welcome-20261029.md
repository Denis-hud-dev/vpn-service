# Подключение зарегистрированного набора — 29 октября 2026

Официальный getStickerSet(KudaVPNPremium) вернул30 video custom emoji. Для каждого вызван getFile и SHA256 скачанного WEBM сопоставлен с исходным файлом. Все16 новых файлов совпали побайтово, старые14 ID/файлов также сохранены. Порядок и Unicode emoji для определения иконки не использовались. Публичное доказательство: assets/telegram-emoji-extension/registration.json (без токенов). Основной runtime-реестр: bot/premium_emoji.py,30 уникальных ID; manifest/mapping.csv синхронизированы. integration_enabled=false в asset manifest означает отсутствие автоматического импорта ассетов; бот использует существующий opt-in Config.pilot_custom_emoji_enabled и собственный Python-реестр.

Основные назначения: покупка→premium,подписка→subscription,подключение→connect,поддержка→support,информация→info,управление→settings,продление→renew,назад→back,главное меню→home. Платформы сохранены в PLATFORM_EMOJI без новых кнопок. Не создавались каналы, чаты, промокоды или неработающие функции.

Официальное поле icon_custom_emoji_id используется в InlineKeyboardButton и KeyboardButton. Право зависит от дополнительного username Fragment либо Telegram Premium владельца для непосредственно отправленных ботом сообщений. Отказ API сохраняет Unicode fallback. Клиент определяет проигрывание анимации; на старых клиентах/при отключённой анимации возможен статичный значок или обычный текст. Для HTML custom emoji alt взят из фактического ответа Telegram, поскольку entity должна охватывать именно связанный обычный emoji. Источники: https://core.telegram.org/bots/api#keyboardbutton ; https://core.telegram.org/bots/api#inlinekeyboardbutton ; https://core.telegram.org/api/custom-emoji

Нижняя ReplyKeyboard получает фирменные значки и текст без дублирующего Unicode. Обработчики и безопасная очистка принимают как новые чистые подписи, так и прежние подписи с emoji; состояние и callback-маршруты сохранены. Первый /start нового DB-пользователя по существующим User/onboarding данным: прежний баннер с новой приветственной caption, преимущества и три шага (выбрать тариф/оформить подписку/Happ); сразу затем компактный tracked экран с покупкой первой строкой. Caption хранится с баннером как важное первое приветствие. Повторный start —только актуальный компактный экран/keyboard, без caption и баннера. Аккаунт владельца не сбрасывался.

Тарифы199/499/1790, независимый сайт/ЮKassa, UUID/Happ/продление/НПД не меняются. Прямая внешняя оплата и Stars в Telegram не добавляются; caption честно сообщает, что оплата в боте не принимается.

Безопасное развёртывание: clone ровно текущего TEST runtimee3bbf21, overlay6 UI-файлов (main keyboard/screens/premium_emoji/start/navigation/reply keyboard), прежний main/backend/schema0008/env. Допущен только конкретный optional kw-only custom_emoji=False и два reviewed новых UI-helper. Старые сигнатуры и остальные bytes охраняются; подготовлены отдельные DB/ledger/config backup0600/root0700 и rollback. Сайт/Monitor/production/DO/VPN не переключаются. 110 targeted checks passed; registered alt regression included. Итог CI и rollout будет записан после фактического выполнения.

## Подтверждённые новые ID

| File | custom_emoji_id | SHA256 match |
|---|---|---|
| info.webm | 5411325387533758442 | exact |
| settings.webm | 5411542884677627480 | exact |
| apple.webm | 5411293969847985936 | exact |
| windows.webm | 5411597757179799385 | exact |
| android.webm | 5411223601103807863 | exact |
| linux.webm | 5411076635912875726 | exact |
| ios.webm | 5411308005801107463 | exact |
| macos.webm | 5411262165615159271 | exact |
| smart_tv.webm | 5411237383653860391 | exact |
| promo.webm | 5408976804927025450 | exact |
| referral.webm | 5411207774149324459 | exact |
| channel.webm | 5411603482371205636 | exact |
| community.webm | 5411454034689172905 | exact |
| notification.webm | 5411311111062464648 | exact |
| back.webm | 5411262105485618987 | exact |
| home.webm | 5411359785926828968 | exact |
