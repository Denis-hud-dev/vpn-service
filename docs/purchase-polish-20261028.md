# Покупка и приватное подключение: точечная доработка

28 октября 2026, America/Los_Angeles. PR29, без новой платёжной системы.

## Реализация

В тестовом Premium-каталоге месячный/квартальный/годовой тарифы199/499/1790 теперь имеют нативные styles primary/success/primary; квартальный помечен «Рекомендуем». Legacy/default каталог и цены не менялись. Карточка тарифа показывает действительные label/months/price из PILOT_TARIFFS, сохраняемый профиль, отсутствие созданного заказа и оплаты внутри бота. Это не платёжное подтверждение и не фиктивный заказ.

Telegram требует Stars для цифровых услуг внутри бота/Mini App даже при наличии сайта: [FAQ](https://core.telegram.org/bots/payments-stars#faq), [условия6.2](https://telegram.org/tos/bot-developers#6-2-digital-goods-and-services). Поэтому запрет внешнего checkout steering сохранён: нет URL оплаты, login/payment redirect, invoice или покупки из бота. Stars выключены и не показаны. Самостоятельный сайт покупатель открывает независимо; бот управляет уже существующим доступом. Кнопка подключения не ведёт на кассу.

Нативные синий primary и зелёный success подтверждены [InlineKeyboardButton.style](https://core.telegram.org/bots/api#inlinekeyboardbutton), aiogram уже передаёт эти поля. Существующий fallback убирает стили при API-отказе, произвольные HEX или имитация цветными картинками не добавлялись. Физическое отображение зависит от актуального Telegram-клиента и ещё проверяется владельцем.

Premium connect не содержит bearer URL в тексте. При существующей page capability первая кнопка «Подключиться через Happ» ведёт на прежнюю страницу, «Открыть подписку» сохраняет исходный feed. На странице прежние copy/QR/Android/iPhone инструкции. Без page capability остаётся feed-кнопка без выдуманного QR. UUID, токен, URL, протокол выдачи и owner guard не меняются. Автоматический импорт не объявляется проверенным на устройстве. Kuda Status показывает «заканчивается», когда реальный срок не более3 дней; expiry остаётся авторитетным.

Сайт сохраняет confirmation→прежний hosted YooKassa→server verification→единственный grant. Добавлена кнопка «Личный кабинет» на панели результата, короткие честные состояния pending/canceled/expired и ошибка refresh без повторной оплаты; retry/backoff неизменны. СБП явно недоступна в sandbox, карта прежняя; live-методы не включены. Один User/Subscription/UUID/Happ, новая база/заказная система не создаётся.

## Проверка и развёртывание

186 профильных тестов passed/1 Mac SO_PEERCRED skip: catalog styles/cards, UI navigation/notification preservation, приватное отображение ссылки, active/near-expiry/expired, checkout confirmation/cancel/timeout/retry, существующая dedup/renewal/Happ/owner isolation. Полный локальный аудит не запускался.

Подготовлен повторный минимальный overlay: clone текущего `app-purchase-menu-f4f3f875856f`, только screens.py, keyboards/tariffs.py и presentation mapping premium_emoji.py. Остальные bytes/backend/env/workers неизменны. Проверка signatures, текущего98 override, UID/testDB/owner, readiness через /ready, backup текущего dropin+onboarding+DB, clone import/цвета/private-link probe под bot UID. Откат восстанавливает **предыдущий98 override**, не удаляет его и не возвращает слишком старую версию. Website-only helper прежний. Порядок: greenCI → immutable staging → bot overlay → website backup/activation → protected/canonical/Happ checks. Ниже результат обновляется только после фактического успеха.

Ручная проверка: /start → Купить VPN → синий/зелёный/синий, карточка3 месяцев499; Моя подписка→подключение без длинной ссылки, на страницеcopy/QR; самостоятельный сайт→выбор тарифа→confirmation→карта, СБПdisabled. Новую оплату ради UI не выполнять: прошлый owner199 ₽ E2E подтверждён. До продаж остаются live Card/SBP/refund, реальный процесс регистрации/доставки НПД-чека, документы, multi-user readiness и устойчивое фоновое сопровождение платежных ошибок. Публичный webhook/сеть не меняются.


CI заметка: один workflow на a5bcd16 прошёл510/3, второй получил RST вместо EOF в существующем disposable TCP-process тесте (не рабочий Xray). Read() после убийства listener может законно вернуть EOF или ConnectionResetError в зависимости от accept race. Проверка уточнена только в тесте: оба подтверждают разрыв, получение данных/timeout всё ещё ошибка, второй процесс/соединение по-прежнему обязаны оставаться живыми. Узкий тест passed; производственный node code не менялся. Временные локальные known_hosts/askpass отсутствовали; host key точно сверён с прежним подтверждённым Proxmox fingerprint, pinned trust и helper из существующего private passphrase восстановлены без вывода секрета и без изменения сервера.


## Фактический результат

Развёрнута source0d45896c9317e61ecb250c8c1ddcdf3bf5c8c6e9; обаCIsuccess510/3. Bot cwd `/opt/kudavpn-telegram-pilot/app-purchase-menu-0d45896c9317`, website cwd `/opt/kudavpn-website-review/site-release-0d45896c9317`. Проверенные backups/rollback0700: `/var/backups/kudavpn-telegram-pilot/menu-0d45896c9317/` и `/var/backups/kudavpn-website-review/site-0d45896c9317/`, восстанавливают прежнююf4f3f87 UI-версию. Shared runtime/bridge/exporter/DB schema не переключались.

Установленные стили/карточки/redacted link/фирменный emoji проверены под реальным UID. Read-only postcheck: bot+site ready, canonical records/Happ URL unchanged, реальный HTTPSfeed200/1testnode10443, protected services/config/listeners/Monitor unchanged. Рабочие Xray не перезапускались, production/DO/платежи/подписки не менялись.

Браузерная сессия сохранилась без новогоOAuth. Реальный кабинет active/expiry и прежний successful order доступны. Проверены /account CTA, confirmation3м499 сcard enabled/SBPdisabled, отсутствиеStars, copy/QR/Android+iPhone на /connect, 375/390/1440px безoverflow. Новых заказов/оплат не создавали. Viewport reset, вкладка оставлена в account. Безопасный screenshot: `/private/tmp/kudavpn-purchase-review-mobile-20261028.png`. Цвета и переносы в физических Telegram Mac/Android ещё проверяет владелец; автоматический импорт Happ не объявляется проверенным на устройстве.
