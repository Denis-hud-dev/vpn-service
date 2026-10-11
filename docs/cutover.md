# Контролируемое переключение KudaVPN: Mac → DigitalOcean

Стоп Mac-бота и переключение server DATABASE_URL допускаются только после явного
подтверждения владельца. Подготовительные проверки/backup сами polling не запускают.
Сервер: 209.38.196.186. Подготовленный release:
`/opt/kudavpn/releases/ba2f0f6a3daf5400`. Вход root по существующему SSH-ключу,
BatchMode=yes, StrictHostKeyChecking=yes. Не менять SSH/UFW/WireGuard/Docker daemon.

## До подтверждения

- Mac: bot/postgres healthy, один Docker polling, нет local Python polling.
- Server: только postgres healthy на 127.0.0.1:5433; 18080 свободен; bot отсутствует.
- Проверить Compose config --quiet, закреплённый release image, SHA256 прежнего dump,
  revision=0002_active_screens, valid active_screens, одинаковый токен (только хеш).
- Сохранить pg_dump server staging-БД и SHA256 в server release/backups/ с mode 600.
  Сохранить защищённую копию .env.production для возврата на staging.
- Не публиковать compose config/env/Config.Env, не передавать секреты через Git.

## После подтверждения, строго последовательно

1. Записать ID/StartedAt/state локальных bot/postgres, hash .env; проверить серверный
   bot ещё не запущен. Остановить ТОЛЬКО Mac bot: `docker compose stop -t 45 bot`.
   Проверить exited и отсутствие Python/Docker polling. Не выполнять down/down -v.
2. Запустить `scripts/verify_backup.py` с локальными env. Он использует ps -a,
   допускает остановленный bot, не запускает polling, создаёт новый pg_dump со
   snapshot и проверяет восстановление в временной PostgreSQL без сети.
   Сверить 7 таблиц, хеши записей, active_screens, revision и ограничения.
3. Передать финальный dump/checksum/verification report через scp с проверкой host key.
   Проверить SHA256 на сервере. Не заменять прежнюю staging-БД и backup.
4. Создать НОВУЮ пустую БД `kudavpn_live_<UTC timestamp>` в существующей server
   PostgreSQL. Восстановить туда pg_restore --single-transaction --exit-on-error
   --no-owner --no-privileges. Перед любой записью проверить, что новая БД пуста.
   Не применять DROP/--clean и не удалять volumes.
5. Проверить имя набора таблиц, row counts, хеши всех записей против финального
   dump/snapshot, alembic_version, ownership/FK active_screens, message IDs >0.
   Не выводить реальные Telegram ID/message ID или записи пользователей.
6. В серверном .env.production атомарно поменять только database-name в DATABASE_URL
   и DATABASE_URL_DOCKER на новую БД; токен/пароль/POSTGRES_DB/прочие значения оставить.
   Сохранить права 600, прежний env уже скопирован для rollback. POSTGRES_DB остаётся
   служебной БД контейнера; pg_isready проверяет сервер, bot/migrate используют live DSN.
7. Из release выполнить только migrate с KUDA_RELEASE_ID=ba2f0f6a3daf5400, без build:
   `docker compose --env-file .env.production -f docker-compose.production.yml run --rm --no-deps migrate`.
   Проверить current/head и Database.check/ping отдельно без polling.
8. Снова проверить отсутствие Mac и server polling. Запустить только server bot:
   `docker compose --env-file .env.production -f docker-compose.production.yml up -d --no-deps --no-build bot`.
   PostgreSQL не пересоздавать. Проверить один процесс/контейнер, fixed image ID,
   /health и /ready =200, startup и отсутствие polling_failure/TelegramConflictError.
9. Проверить Docker restart policy bot/postgres=unless-stopped и docker enabled.
   Не перезапускать Docker/сервер: это затронуло бы VPN и не нужно для проверки настроек.
   /ready не гарантирует успешный polling; нужны логи и реальные сообщения Telegram.
10. Offline smoke проверяет /start/navigation/repeat start без второго polling;
    затем владелец отправляет /start в Telegram, проверяет шесть разделов, назад,
    повторный /start и один экран. Сопоставить новые server timings и active_screens,
    подтвердить обработку на сервере. Не использовать getUpdates отдельным клиентом.
    Не менять webhook/drop_pending_updates во время cutover.

## Откат при любой неудаче

- До запуска server bot: серверный polling остаётся отсутствующим. Вернуть сохранённый
  server env; сохранить новую БД/dump для диагностики. Убедиться в отсутствии server
  polling, затем запустить точный сохранённый контейнер Mac через `docker start
  <saved-bot-container-id>` с прежним image/.env/volume. Это не запускает зависимости
  Compose и не пересоздаёт PostgreSQL или контейнер миграций.
- После запуска server bot: сначала остановить ТОЛЬКО server bot и убедиться, что
  он exited. Сохранить свежий server live dump перед решением о возврате.
- Если доказано, что server live БД не получила новых записей/изменений, можно вернуть
  Mac на сохранённую локальную БД после проверки отсутствия server polling.
- Если изменения есть или неизвестно, не включать Mac на старой БД: проверить свежий
  server dump, восстановить в новую отдельную локальную БД и указать её для одного
  Mac bot, сохраняя token и исходный volume. Если безопасный перенос назад невозможно
  завершить, оставить ОБА polling остановленными, сохранить данные и сообщить владельцу.
- Никаких schema downgrade, удаления данных или VPN/SSH/UFW изменений при откате.

Необратимого удаления здесь нет. Граница простого отката — первая новая запись
от server bot: после неё возврат требует сохранения и обратного переноса актуальных
данных, а не запуска прежней Mac-базы. Только один polling при любом переходе.
