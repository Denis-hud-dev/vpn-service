# KudaVPN

KudaVPN — коммерческий VPN-сервис с управлением через Telegram на Python 3.12 и aiogram 3. Второй этап добавляет PostgreSQL, миграции и слой бизнес-сервисов. Реальные платежи и VPN-доступ ещё не подключены.

## Что работает

Главное меню и тарифы сохранены: **1 месяц — 299 ₽**, **3 месяца — 799 ₽**, **12 месяцев — 2490 ₽**. Выбор тарифа показывает прежнюю заглушку; он не создаёт платёж и не активирует подписку.

При заполненном `DATABASE_URL` первый `/start` создаёт пользователя в PostgreSQL, последующие вызовы обновляют имя и username. Создание атомарное через UPSERT; Telegram ID уникален. `referrer_id` сохраняется при первом обращении и не меняется; саморефералы игнорируются. Это Telegram ID, а не внешний ключ: приглашение может прийти раньше регистрации пригласившего пользователя.

«Моя подписка» и «Мои устройства» читают базу. У нового пользователя нет подписки и устройств. `SubscriptionService` поддерживает `pending`, `active`, `expired`, `blocked`, проверяет даты активации и допустимые переходы, предоставляет массовое завершение просроченных подписок `expire_due()`. Открытие подписки также переводит просроченную `active` в `expired`. Периодический scheduler пока не подключён; VPN-сервис отдельно проверяет срок действия.

При пустом `DATABASE_URL` сохраняется рабочий деморежим первого этапа с данными в памяти. При указанном, но недоступном URL бот не переключается незаметно в деморежим: запуск требует доступной базы и применённых миграций.

## Структура

```text
bot/
  main.py                  # polling, lifecycle, /health и /ready
  config.py                # конфигурация из environment
  db/
    models.py              # User, Subscription, Device, VPNServer, Payment
    session.py             # engine, async session factory, проверка базы
  handlers/                # существующие русские экраны и кнопки
  keyboards/               # меню и тарифы
  services/
    __init__.py            # тарифы, интерфейсы, demo/persistent factory
    users.py               # регистрация и обновление Telegram-пользователя
    subscriptions.py       # жизненный цикл подписки
    devices.py             # список устройств
    payments.py            # pending payments, идемпотентность, YooKassa boundary
    vpn.py                 # проверки подписки, отключённый WireGuard adapter
  requirements.txt
  requirements-dev.txt
  Dockerfile
migrations/
  env.py
  script.py.mako
  versions/0001_initial_initial_kudavpn_schema.py
alembic.ini
docker-compose.yml
tests/
  smoke.py
  test_database.py
.env.example
.gitignore
.dockerignore
```

## База данных

Все модели имеют UUID, `created_at`, `updated_at`; временные поля PostgreSQL хранят timezone. Связи и ограничения проверяются базой, удаление связанного пользователя или сервера ограничено `RESTRICT`.

| Модель | Основные поля и правила |
| --- | --- |
| User | Уникальный `telegram_id`, профиль, `referrer_id` первого обращения |
| Subscription | Одна текущая подписка на пользователя, tariff ID, status, starts/expires, device_limit |
| Device | Пользователь, подписка, сервер, платформа, статус, публичный ключ, IP; составной FK проверяет владельца подписки |
| VPNServer | Имя, hostname, порт, локация, публичный ключ, подсеть; по умолчанию выключен |
| Payment | Пользователь, необязательная подписка, тариф, сумма целым числом в копейках, RUB, provider ID, idempotency key, статус |

Уникальность `(provider, provider_payment_id)` и `idempotency_key` подготовлена для будущей обработки оплаты. История продлений будет храниться в платежах; отдельная история изменений подписки пока не реализована. Для активного устройства обязательны сервер, публичный ключ и IP; пара сервер/IP уникальна. Приватные WireGuard-ключи и API-ключи в таблицы не записываются. Лимит устройств пока хранится как настройка (по умолчанию 5); выдачи устройств, проверяющей этот лимит, ещё нет.

## Переменные окружения

Настоящий `.env` исключён из Git и Docker build context. Не перезаписывайте существующий `.env`: добавьте новые поля вручную. Если файла ещё нет, создайте копию `.env.example` локально. Приложение читает environment variables и само `.env` не загружает.

- `BOT_TOKEN` — локальный токен BotFather; обязателен для бота, не нужен для миграций.
- `SUPPORT_USERNAME` — необязательный Telegram username поддержки.
- `POSTGRES_DB`, `POSTGRES_USER` — имя базы и пользователь (пример: `kudavpn`).
- `POSTGRES_PASSWORD` — придуманный локальный пароль; Compose требует непустое значение.
- `DATABASE_URL` — для запуска бота и Alembic на хосте, драйвер **postgresql+psycopg**.
- `DATABASE_URL_DOCKER` — отдельный URL с hostname `postgres`; Compose передаёт его контейнерам как `DATABASE_URL`.
- `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY`, `YOOKASSA_RETURN_URL` — пока оставьте пустыми.

Шаблоны URL (замените значения локально, пароль URL-encode при наличии специальных символов):

```text
DATABASE_URL=postgresql+psycopg://USER:ENCODED_PASSWORD@127.0.0.1:5432/DB
DATABASE_URL_DOCKER=postgresql+psycopg://USER:ENCODED_PASSWORD@postgres:5432/DB
```

В `.env`, который загружается через shell `source`, заключайте значения со специальными символами в одинарные кавычки. Для простоты настройки можно использовать длинный случайный буквенно-цифровой пароль. Не вставляйте реальные секреты в README или команды, сохраняемые в истории терминала.

## Локальный запуск с PostgreSQL

Из корня репозитория, после заполнения локального `.env`:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r bot/requirements.txt
set -a
source .env
set +a
```

Если `.venv` уже есть, достаточно активировать её и обновить зависимости. PostgreSQL можно запустить через Docker Compose; для этих команд также заполните `DATABASE_URL_DOCKER`, поскольку Compose проверяет переменные всего файла:

```sh
docker compose up -d postgres
alembic upgrade head
alembic current
python -m bot.main
```

Откройте Telegram и отправьте `/start` — пользователь появится в `users`. Посмотреть количество пользователей без вывода токенов:

```sh
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT count(*) FROM users;"'
```

Если Docker отсутствует, используйте уже установленный PostgreSQL и заполните `DATABASE_URL`. Для деморежима оставьте `DATABASE_URL` пустым. Не запускайте два polling-процесса с одним токеном. Остановка локального бота: `Ctrl+C`.

## Полный запуск через Docker Compose

Остановите прежний локальный бот, заполните переменные и выполните:

```sh
docker compose up --build -d
docker compose logs -f bot
```

Порядок запуска: PostgreSQL проходит healthcheck → контейнер `migrate` применяет Alembic → бот стартует только после успешной миграции. База сохраняется в named volume `postgres_data`. Пароль существующей базы не меняется автоматически при изменении `.env`.

```sh
docker compose stop bot
docker compose down
```

`down` без `-v` сохраняет данные. После следующих изменений кода/миграций выполните `docker compose up --build -d`.

Контейнер бота работает непривилегированным пользователем. SIGINT/SIGTERM закрывают polling, Telegram-сессию, storage, health-сервер и SQLAlchemy engine. PostgreSQL публикуется только на `127.0.0.1:5432`. HTTP-порт бота по умолчанию наружу не публикуется.

`/health` на 8080 проверяет живой процесс. `/ready` требует завершённого старта и доступной мигрированной базы, возвращая 503 при сбое базы. Проверка не гарантирует доступность Telegram при последующем сетевом сбое. Для локальной диагностики можно добавить публикацию `127.0.0.1:8080:8080`. При изменении `HEALTH_PORT` также обновите Docker healthcheck.

## YooKassa и WireGuard: подготовленная архитектура

`PaymentRepository.create_pending()` сохраняет платёж с ценой в копейках и ключом идемпотентности; повторный вызов с тем же ключом и параметрами возвращает ту же запись. Telegram-кнопки пока этот метод не вызывают. `PaymentGateway` описывает создание и проверку платежа; `YooKassaGateway` сейчас всегда выдаёт ошибку «не подключена». Реальные ключи не читаются и запросы оплаты не отправляются.

Следующий этап оплаты: HTTP adapter YooKassa/СБП, confirmation URL, проверка статуса у провайдера, безопасный webhook, идемпотентная обработка событий и транзакционная активация/продление подписки. `SubscriptionService.set_status()` предназначен для доверенного backend-кода, он не является подтверждением оплаты и не доступен пользователю Telegram.

`WireGuardGateway` описывает добавление/удаление peer; текущий адаптер выключен. `VPNService` проверяет подписку и наличие включённого сервера, но даже такая запись в базе пока не выдаёт доступ: `provision_device()` всегда отказывает. Далее нужны adapter управления сервером, адресный пул, лимиты устройств, безопасная передача конфигурации, повторные попытки и отзыв peer при блокировке/истечении. Пока переход статуса в базе не отзывает реальный peer — реальных peer на этом этапе нет.

## Проверки

```sh
pip install -r bot/requirements-dev.txt
python -m compileall -q bot migrations tests
python tests/smoke.py
python -m pytest -q
pip check
```

Smoke test проверяет прежние экраны и callback-кнопки без Telegram API. Интеграционный тест на временной SQLite проверяет миграции `upgrade → downgrade → upgrade`, совпадение схемы с моделями, сохранение пользователей/referrer_id, статусы подписки, ограничения FK, устройства, pending payments и отключённые внешние адаптеры. SQLite используется только в тестах; production-конфигурация допускает PostgreSQL.

Проверка PostgreSQL SQL без подключения (при заданном URL):

```sh
alembic upgrade head --sql > /tmp/kudavpn-schema.sql
```

Для интеграционного теста на настоящем PostgreSQL задайте **отдельную пустую тестовую базу** через `TEST_DATABASE_URL=postgresql+psycopg://...` в локальном окружении и выполните `python -m pytest -q`. Тест применяет и откатывает таблицы: не используйте рабочую базу. Без этой переменной PostgreSQL-тест явно пропускается.

Подход к асинхронным сессиям и миграциям основан на [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) и [Alembic cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).
