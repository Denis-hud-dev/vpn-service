# Telegram management и самостоятельная покупка KudaVPN

27 октября2026, America/Los_Angeles. Продолжение PR29, без новой платёжной системы.

## Интерфейс бота

Четыре Premium-действия: «💎 Купить VPN», «◈ Моя подписка», «🚀 Подключиться через Happ», «💬 Поддержка». Постоянная нижняя клавиатура, EROFS fix и гибридная история сохранены. Smart перемещён в «Управление», старые callback работают. В production/default меню код не менялся.

«Купить VPN» открывает каталог199/499/1790 ₽ за1/3/12 месяцев, **не оплату**. Экран честно сообщает, что бот платежей не принимает. Нет purchase URL, invoice, web_app или скрытого перенаправления. Старые paytest/paymentpreview/paymentinfo callbacks не создают заказ и не показывают Stars/XTR. Неработающий preview также больше не содержит этих пунктов. Offline Stars prototype остаётся незарегистрированным/выключенным; его сохранённые тестовые цены не являются предложением пользователям.

[Telegram требует Stars для цифровых услуг внутри ботов/Mini Apps](https://core.telegram.org/bots/payments-stars#faq), даже при наличии внешнего сайта. Решение владельца исключить их из продукта не отменяет это ограничение: рублёвая продажа остаётся самостоятельным сайтом, без steering из бота. Полноценная внешняя покупка непосредственно кнопкой Telegram **не включена**.

## Сайт и методы

Сайт показывает банковскую карту и явно недоступную в sandbox СБП. ЮMoney больше не рекламируется интерфейсом KudaVPN. `website_methods` использует существующий ChannelPolicy: test-shop никогда не включает СБП; будущая live eligibility требует подтверждённой merchant capability и отдельного включения канала. read-only режим выключает обе возможности.

**Окончательный выбор происходит в hosted форме ЮKassa**. Сайт не обещает preselection по неподтверждённому URL-параметру и не меняет payload уже созданного заказа. Единственная тестовая активная кнопка открывает прежний Умный платёж; форма sandbox самого провайдера может дополнительно предлагать ЮMoney — KudaVPN этим не управляет. Для строгого набора live-методов остаётся подтвердить фактические методы магазина. Нельзя считать отключённую в sandbox СБП протестированной реальной операцией. Боевой клиент/ключи/списания не включались.

Одна каноническая `User`/`Subscription`: бот получает identity из доверенного Telegram update, сайт — из проверенной OAuth session, payment owner только из server-side account. Существующие metadata/amount/API verification/unique payment grant и календарное продление не переписаны. UUID, feed URL и Happ не переиздаются. Сайт и бот читают одинаковые тариф и expiry; это проверено интеграционным тестом web payment → bot snapshot, включая duplicate без второго срока. Новый реальный sandbox E2E не нужен: предыдущий owner199 ₽ уже успешно завершён, финансовая логика этой задачей не меняется.

## Проверки и безопасное внедрение

197 профильных tests passed,1 локальный Linux SO_PEERCRED skip. Проверены четыре действия, история/переходы/старые callback, отсутствие Stars/payment link/invoice, методы Card/SBP, чужой доступ, expiry/duplicate/renewal/Happ regression и emoji/style fallback. Дополнительно2 overlay tests passed. Полный локальный аудит не запускался; полный CI проверяется в PR29.

Тестовый бот сейчас использует `/opt/kudavpn-telegram-pilot/app-checkout-08fbff6`, общий с прежним checkout; его каталоги уже199/499/1790. Нельзя заменять весь bot runtime веткой сайта с другим DB head. `deploy/telegram-pilot/update_menu.py` создаёт отдельный клон **текущего** app и переносит только3 UI-файла: keyboards/main, screens, handlers/subscription. Public signatures сравниваются; остальные bytes совпадают. User/credentials/env/schema/payments/workers/Happ остаются прежними, onboarding SQLite backup сохраняет EROFS-настройку. Перед restart — import под реальным bot UID. Изменяется только WorkingDirectory через98-purchase-menu.conf и restart только тестового бота; при ошибке rollback. DB/source configs/VPN не мигрируются.

Порядок: после successful CI подготовить immutable website release без включения, сначала guarded bot UI overlay, затем отдельный guarded website-only update. Так website baseline фиксирует уже новый разрешённый bot PID, а не принимает его смену за регрессию. Общий website current остаётсяfaca30b, bridge/exporter/checkout не переключаются. Backups/private manifests и итоговый runtime фиксируются в handoff после фактической установки; этот документ сам по себе не подтверждает deployment.

Для ручной приёмки: `/start` в @KudaVPN_Test_Bot → четыре действия → каталог → тариф → «Назад» → «Моя подписка» (текущий срок) → Happ → поддержка. Проверить нижнюю клавиатуру/отсутствие новых Stars и оплаты из бота. Самостоятельный сайт открывать отдельно; новая оплата не требуется для проверки UI. Production/DO/Monitor/Xray и реальные продажи не затрагивать. До запуска остаются live карта/СБП/refund, фактический НПД-чек/доставка, документы и multi-user admission.
