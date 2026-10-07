# KudaVPN

KudaVPN — коммерческий VPN-сервис с управлением через Telegram. Этап 1: рабочий интерфейс бота на Python 3.12 и aiogram 3; подписки, устройства и оплата пока демонстрационные.

## Структура

```text
bot/
  main.py             # polling, logging, lifecycle, health/readiness
  config.py           # переменные окружения
  handlers/           # start, subscription, devices, instructions, referral, support
  keyboards/          # main, tariffs
  services/           # тарифы и заменяемые сервисы
  requirements.txt
  Dockerfile
tests/smoke.py        # проверки через Dispatcher без Telegram API
.env.example
.gitignore
.dockerignore
```

## Локальный запуск

Из корня репозитория:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r bot/requirements.txt
cp .env.example .env
```

Заполните `BOT_TOKEN` в `.env` токеном своего бота, а `SUPPORT_USERNAME` — username поддержки, например `kudavpn_support`, без URL. Пустая поддержка показывает заглушку. Настоящий `.env` исключён из Git и Docker build context. Не публикуйте токен.

Бот читает только environment variables и сам не загружает `.env`. Для загрузки файла в текущий shell (файл должен содержать корректные shell-присваивания):

```sh
set -a
source .env
set +a
python -m bot.main
```

Пустой `BOT_TOKEN` останавливает приложение с понятной ошибкой. Будущие `DATABASE_URL` и `YOOKASSA_*` оставьте пустыми: пока они не используются. Не запускайте одновременно несколько polling-процессов с одним токеном.

## Docker

```sh
docker build -f bot/Dockerfile -t kudavpn-bot .
docker run --name kudavpn-bot --env-file .env --restart unless-stopped -d kudavpn-bot
docker logs -f kudavpn-bot
docker stop kudavpn-bot
```

Контейнер работает от непривилегированного пользователя. SIGINT/SIGTERM останавливают polling, закрывают HTTP-сессию, storage и health-сервер. `/health` на порту 8080 проверяет живой процесс; `/ready` возвращает 200 после успешного `getMe` при запуске и 503 до готовности. Это проверка инициализации, она не гарантирует доступность Telegram при последующем сетевом сбое. Порт не публикуется наружу; для локальной диагностики добавьте `-p 127.0.0.1:8080:8080`. При изменении `HEALTH_PORT` нужно также изменить Docker healthcheck.

## Реализовано

- Русское меню, `/start`, переходы кнопками и «Назад» на внутренних экранах.
- Три тарифа: 299, 799 и 2490 ₽; выбор показывает заглушку оплаты.
- Неактивная подписка и пустой список устройств.
- Выбор инструкции для iOS, Android, Windows и macOS.
- Персональная ссылка `https://t.me/KudaVpnBot?start=ref_TELEGRAM_ID` (username берётся из `getMe`, поэтому другой бот тоже поддерживается).
- Разбор `referrer_id`, запрет саморефералов, первое касание в памяти; перезапуск очищает данные, бонусы не начисляются.
- Поддержка через `SUPPORT_USERNAME`, обработка неизвестных сообщений и устаревших кнопок.
- Логирование, lifecycle и Docker healthcheck. Обработка остановки следует [документации aiogram](https://docs.aiogram.dev/en/latest/dispatcher/dispatcher.html).

## Следующие этапы

Подключить backend и PostgreSQL с миграциями и постоянным хранением пользователей/referrer_id; YooKassa и СБП через реализацию `PaymentService`, проверку webhook, идемпотентность платежей и активацию подписки; WireGuard, выдачу конфигураций и управление устройствами; реальные инструкции и бонусные дни. Цена и длительность тарифа централизованы в `bot/services`, обработчики получают сервисы через dependency injection.

## Проверки

```sh
python -m compileall -q bot tests
python tests/smoke.py
pip check
```

Smoke test использует синтетические обновления и подменённую Telegram-сессию: реальные токены, сеть и платежи не нужны.
