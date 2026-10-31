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


## Фактически развёрнуто — 29 октября2026

Source66a74f25944562b5551dad976626c1a820666942 опубликован в существующем Draft PR29. Оба CI success:546 passed/3 skipped (нет TEST_XRAY_BINARY). Локально111 различных профильных проверок (110 + исправленная legacy Smart Back expectation). Долгие наборы вручную повторно не запускались. Картинки/анимации не генерировались; все16 соответствий установлены точным SHA256 официальных getStickerSet/getFile файлов.

TEST bot runtime /opt/kudavpn-telegram-pilot/app-purchase-menu-66a74f259445: clone предыдущегоe3bbf21 с overlay6 UI-файлов. Полный PR29 с schema0010 как бот не запускался. Все остальные bytes/backend/env/schema0008/баннер прежние. Website runtime0d45896/bridge/exporter/Monitor/DO/production/Xray/UUID/Happ/платежи не менялись. До/после activation canonical financial/profile digest одинаков; protected units/configs/listeners и site/Monitor PID прежние.

Backup root0700 /var/backups/kudavpn-telegram-pilot/menu-66a74f259445, DB/ledger/unit copies0600. rollback.sh0700 возвращает только тестового бота на e3bbf21; pg_restore list/decode/sh-n проверены. Автоматически восстанавливать stale DB поверх новых событий запрещено. При ошибке activate предусмотрен auto rollback; не потребовался. validation.json содержит safe outcomes.

В Telegram Desktop Mac реальный повторный start показал все5 фирменных inline icon и3 reply icon, первое место/отдельная строка покупки сохранены. Реальные текстовые команды новой ReplyKeyboard «Информация» и «Личный кабинет» отработали; экран кабинета показал существующую активную подписку/тариф/срок/узел и значки renew/settings/back. Важные уведомления не затрагивались. Первое приветствие проверено автоматически на новом User/onboarding fixture, состояние владельца не сбрасывалось. Физический новый пользователь и Android/iOS ещё вручную. Координатные клики inline CUA Mac ранее ненадёжны; маршруты всех основных кнопок покрыты автотестами, не заявляется физическая проверка каждого нажатия.

После обновления: bot ready,0 startup ERROR/traceback, HTTPS site ready, существующий HTTPS feed200/1VLESS/test10443, protected state unchanged. Contact поддержки сейчас не настроен, в информации это честно указано; адрес/канал не выдумывались. На стороне владельца остаётся проверить анимацию и переходы Android/iOS и при необходимости сообщить будущий контакт поддержки. Не ослаблялись owner access gates или Telegram/payment проверки.

Снимки только локально (не публиковать реальные сроки владельца в Git): /private/tmp/kudavpn-registered-emoji-menu-20261029.png и /private/tmp/kudavpn-registered-emoji-account-20261029.png. Таблица и ZIP в Downloads/KudaVPN-Premium-Emoji-Extension-20261028 обновлены новыми ID, WEBM bytes неизменны, ZIP CRC прошёл. Генерация не повторялась. Этап завершён.
