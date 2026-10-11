# KudaVPN Premium: подготовка переноса на Ubuntu 24.04

Этот документ — план, команды ниже сейчас НЕ выполнялись. Работающий Mac-бот,
рабочая PostgreSQL и сервер WireGuard остаются без изменений. Выполнение переноса
требует отдельного подтверждения владельца.

## Результат диагностики кода

- Обработчики, Telegram и PostgreSQL асинхронны. Блокирующих requests, sleep,
  subprocess или SSH в пользовательских обработчиках нет.
- Кнопка подтверждается до бизнес-обработчика; после перезапуска перед подтверждением
  возможна загрузка active_screens из БД, ограниченная 3 секундами. В дальнейшем
  используется кеш. Все операции одного чата сериализованы: медленный запрос
  Telegram или SQL задерживает следующие нажатия этого пользователя.
- /start выполняет UPSERT пользователя, загрузку экрана при отсутствии кеша,
  редактирование через Telegram и попытку удаления команды. Замер команды включает
  удаление; визуальный ответ может появиться раньше завершения замера.
- Кабинет выполняет несколько SQL-запросов, включая блокировку подписки.
  Потенциальные причины задержек: сеть Mac→Telegram, Telegram rate limits,
  истощение пула БД, блокировки строк, загрузка CPU/RAM/диска Docker Desktop.
  Фактическая причина не установлена: реальные пользовательские задержки не измерялись.
- Добавлены timings start/message/callback, Telegram API (кроме idle getUpdates),
  SQL без текста запроса и параметров. Нет Telegram ID, chat/message ID, username,
  текста сообщения, токенов, URL или DSN. Медленнее 1000 ms — WARNING.
  Логи сторонних библиотек обезличены; traceback из них не записывается.
  Это намеренно ограничивает детализацию диагностики ошибок.
- Обычный Telegram HTTP timeout 10 секунд; polling_timeout=30 используется отдельно
  (aiogram увеличивает timeout long polling). Одновременно до 64 обработчиков.
  SQL pool 5+5, ожидание пула 3 с, connect_timeout 5 с, statement_timeout 5 с,
  lock_timeout 2 с. Это ограничения отдельных операций, не общего времени команды.
  Нет повторной отправки экрана при временном сетевом сбое/rate limit.
- /ready теперь выполняет SELECT 1, полная проверка миграций остаётся на startup.
  /health — liveness. Readiness не доказывает доступность Telegram.

Измерения вступят в силу только после отдельно разрешённой пересборки/перезапуска.
Сравнивайте серию замеров, а не единичный запрос; долгий telegram.editMessageText
указывает на API/сеть, database.query — SQL, большая длительность callback при
быстрых запросах — возможное ожидание блокировки чата/пула. SQL timing не включает
получение соединения. Анонимные строки пока не коррелируются по пользователям.

## Конфигурация сервера

`docker-compose.production.yml` — самостоятельный файл, НЕ override локального.
Используйте всегда `--env-file .env.production -f docker-compose.production.yml`.
Он рассчитан на Linux/Ubuntu 24.04 и уже установленный Docker Engine с Compose v2.

Особенность VPN-хоста: network_mode=host не создаёт bridge/NAT/firewall-правила.
PostgreSQL слушает только 127.0.0.1:5433, health HTTP — только 127.0.0.1:18080.
Никаких публичных ports, privileged, NET_ADMIN, /dev/net/tun, VPN mount или доступа
к docker.sock у бота. Host network снижает сетевую изоляцию контейнеров, поэтому
бот работает без root, capabilities, с read-only filesystem и no-new-privileges.

До запуска проверить свободные 5433/18080, доступность исходящего HTTPS/DNS,
Docker/Compose и автозапуск Docker после reboot, свободные RAM/диск и отсутствие
второго бота в Docker/systemd/ручном Python. Размер сервера и потребление WireGuard
нужно узнать; ресурсы здесь не гарантированы и автоматически не ограничены.
Порты при конфликте должны быть согласованно изменены в файле, URL и healthcheck.

Не устанавливать/перезапускать Docker, не менять daemon.json, iptables/nftables,
ufw, sysctl, SSH, WireGuard или маршруты без отдельного согласования.
Запуск самого Docker Engine может изменить forwarding/firewall на Linux:
[официальная документация](https://docs.docker.com/engine/network/packet-filtering-firewalls/).
Если Docker ещё не установлен, этот план не является разрешением его установки.
Нужно сначала согласовать совместимость с текущим WireGuard и безопасный доступ.

Вручную создать НА СЕРВЕРЕ `.env.production` на основе `.env.example`, chmod 600,
заполнить тот же BOT_TOKEN и SUPPORT_USERNAME через защищённый редактор,
новый длинный пароль PostgreSQL и URL с URL-encoded паролем:
`postgresql+psycopg://USER:ENCODED_PASSWORD@127.0.0.1:5433/DB`.
Это значение нужно в DATABASE_URL_DOCKER, при необходимости также DATABASE_URL.
Не выводить env, docker inspect Config.Env, compose config целиком или токен в CLI.
Секреты не переносить через Git; .env.production уже исключён правилом .env.*.
Текущий Mac `.env` не менять. YooKassa поля оставить как есть/пустыми.

Бот/БД имеют restart=unless-stopped, stop_grace_period и ротацию логов 3×10 MB
на сервис; migrate запускается один раз. Docker healthcheck НЕ перезапускает
сам по себе unhealthy контейнер: restart policy срабатывает при выходе процесса.
Нужен отдельный согласованный мониторинг /ready, отсутствия polling/Conflict,
места на диске, рестартов, времени ответов и проверяемых резервных копий.
Не добавлять второй systemd-процесс polling поверх Docker.
[Restart policies](https://docs.docker.com/engine/containers/start-containers-automatically/).
Для воспроизводимости релиза сохранить проверенный комплект исходников и image digest.
Серверный образ использует requirements-linux.lock с точными версиями и SHA256;
обычный локальный Dockerfile сохраняет прежние диапазоны зависимостей.

## Резервная копия и проверка восстановления (после подтверждения)

Использовать PostgreSQL 16 на источнике и при восстановлении. Не копировать живой
каталог volume с Mac на Linux. pg_dump custom format переносит схему, данные,
constraints и sequences, включая active_screens и alembic_version. Роли и серверные
настройки не включаются; целевая роль создаётся env PostgreSQL, восстановление
использует --no-owner --no-privileges. Учесть отдельно дополнительные роли/расширения,
если они появятся. Dump содержит персональные данные: хранить с mode 600, вне Git,
передавать по защищённому каналу, держать зашифрованную внешнюю копию.

Сначала можно сделать пробную копию без остановки Mac: pg_dump даёт согласованный
снимок, но изменения после него в финальную базу не попадут. После остановки всех
писателей нужен свежий финальный dump. Для локального источника из корня проекта:

```sh
umask 077
mkdir -p backups
docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-privileges' > backups/kudavpn.dump
shasum -a 256 backups/kudavpn.dump > backups/kudavpn.dump.sha256
```

Проверить код завершения pg_dump и размер файла; при ошибке не использовать dump.
На сервере сверить SHA256. Проверка списка pg_restore --list не заменяет восстановление.
Пробное восстановление выполнять в отдельном одноразовом контейнере без сети и
без production volume. Следующие команды — только будущая rehearsal-проверка:

```sh
docker run -d --name kudavpn-restore-check --network none \
  -e POSTGRES_HOST_AUTH_METHOD=trust postgres:16-alpine
# Дождаться pg_isready; не восстанавливать до готовности.
docker exec kudavpn-restore-check pg_isready -U postgres
docker exec kudavpn-restore-check createdb -U postgres restore_check
docker exec -i kudavpn-restore-check pg_restore -U postgres -d restore_check \
  --exit-on-error --single-transaction --no-owner --no-privileges < backups/kudavpn.dump
```

Trust здесь только в изолированном одноразовом контейнере без сети, не production.
После восстановления проверить revision=0002_active_screens, наличие всех таблиц,
constraints/indexes, число записей и контрольные хеши таблиц users, subscriptions,
devices, vpn_servers, payments, active_screens. Read-only пример хеша одной таблицы:
`SELECT count(*), md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY telegram_id)::text,'[]')) FROM users t;`.
Для остальных business-таблиц ORDER BY id, для active_screens ORDER BY user_id.
Сравнение финального источника с восстановленной копией делать при остановленных
писателях. Не выводить строки пользователей. На большой базе вместо jsonb_agg нужен
проверенный потоковый checksum, чтобы не расходовать слишком много памяти.
Удалить только одноразовый kudavpn-restore-check после успешной проверки; source и
целевой volume не удалять. Если проверка не прошла — перенос не продолжать.
[pg_restore](https://www.postgresql.org/docs/16/app-pgrestore.html).

## Переключение одного polling-экземпляра

1. После подтверждения подготовить файлы и образ на сервере. Пока Mac работает,
   запустить ТОЛЬКО целевую postgres, без migrate/bot. Убедиться, что новый volume
   пустой и относится к kudavpn-premium, а не к существующей базе на сервере.
2. Выполнить rehearsal восстановления. Согласовать окно короткого простоя.
3. На Mac `docker compose stop -t 45 bot`. Проверить exited, отсутствие локального
   python -m bot.main и других Docker polling-процессов. Не менять restart-policy
   всех контейнеров; unless-stopped сохраняет остановку этого бота после reboot.
   Остановить также будущие backend/scheduler писатели, если они появятся.
4. Сделать финальный dump и контрольные хеши, передать защищённо, сверить SHA256.
5. Восстановить в НОВУЮ ПУСТУЮ целевую БД. Не использовать --clean, DROP или down -v.
   Команды сервера из корня репозитория:

```sh
docker compose --env-file .env.production -f docker-compose.production.yml up -d postgres
# Перед restore убедиться, что целевая БД пуста; иначе остановиться.
docker compose --env-file .env.production -f docker-compose.production.yml exec -T postgres \
  sh -c 'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_restore -h 127.0.0.1 -p 5433 -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exit-on-error --single-transaction --no-owner --no-privileges' < backups/kudavpn.dump
docker compose --env-file .env.production -f docker-compose.production.yml run --rm --no-deps migrate
docker compose --env-file .env.production -f docker-compose.production.yml run --rm --no-deps migrate alembic current
# Только после сравнения данных, проверки миграций и отсутствия Mac polling:
docker compose --env-file .env.production -f docker-compose.production.yml up -d --no-deps bot
curl --fail http://127.0.0.1:18080/health
curl --fail http://127.0.0.1:18080/ready
```

6. Проверить один server polling-процесс, отсутствие Conflict, health/readiness,
   /start и все разделы, timings; WireGuard проверить существующим способом, не
   менять конфигурацию. Сохранённые message_id переедут вместе с БД при том же токене.
   Деплой не должен вызывать deleteWebhook(drop_pending_updates=True): не терять очередь.
7. Mac остаётся остановленным. Обеспечить резервные копии по расписанию с политикой
   хранения, шифрованием и периодической restore-проверкой; расписание ещё не настроено.

Откат: сначала остановить серверный бот и проверить отсутствие polling, сохранить
копию серверной БД. Если сервер не принял новые записи, можно вернуть Mac с его БД.
Если записи появились, нельзя просто включить старую Mac-базу: перенести свежий dump
обратно в отдельную пустую БД, проверить восстановление и согласовать переключение.
Никаких одновременно работающих polling с одним токеном; отсутствие Telegram Conflict
само по себе не доказывает отсутствие второго процесса. Старый volume и backup
сохранять до приёмки переноса.

## Проверенная локальная процедура и воспроизводимый релиз

`scripts/verify_backup.py` автоматизирует read-only pg_dump и изолированное
восстановление. Запускать из корня локального проекта с загруженными переменными
из существующего .env, например после `set -a; source .env; set +a`:

```sh
python scripts/verify_backup.py
```

Скрипт удерживает REPEATABLE READ / READ ONLY snapshot, передаёт его pg_dump,
сравнивает все семь таблиц и хеши записей с тем же снимком. Поэтому новые /start
во время проверки не дают ложного расхождения. Восстановление идёт в отдельной
PostgreSQL с network=none и временным tmpfs без рабочего volume; контейнер удаляется
в finally. Сравниваются columns, indexes, constraints; эквивалентная запись text-casts
нормализуется только для четырёх простых CHECK статусов/платформ. Dump и SHA256
сохраняются в backups/ с правами 600, каталог создаётся с 700.
Проверка ожидает текущий набор таблиц/revision и локальные bot/postgres сервисы.
Если схема проекта изменится, проверку нужно обновить. Не запускать эту процедуру
как способ финальной синхронизации: работающий бот может записать данные ПОСЛЕ snapshot.
Сейчас это проверенная пробная копия, финальный dump нужен после остановки писателей.

Серверный образ теперь использует `bot/Dockerfile.production`, Python manifest
с фиксированным digest и `bot/requirements-linux.lock`: точные версии существующего
локального окружения, SHA256 Linux x86_64 wheels, установка с --require-hashes.
PostgreSQL manifest тоже закреплён digest. Оба сервиса явно выбирают linux/amd64.
Локальный Dockerfile и работающий образ не заменяются. Lock предназначен для
Python 3.12 Linux x86_64, его нельзя использовать для macOS/ARM.

Чтобы не потерять незакоммиченные файлы, создать проверенный комплект исходников
по явному allowlist, без .env, backups, .git и виртуального окружения. Пример
будущей упаковки (команды не отправляют ничего на сервер):

```sh
tar --exclude='__pycache__' --exclude='*.pyc' -czf /tmp/kudavpn-source.tar.gz \
  bot migrations scripts docs tests alembic.ini docker-compose.production.yml \
  README.md .dockerignore .env.example
shasum -a 256 /tmp/kudavpn-source.tar.gz
```

На Linux SHA256 проверить через sha256sum. Выбрать KUDA_RELEASE_ID по checksum
комплекта, хранить каталог каждого релиза отдельно; .env.production — защищённый
серверный файл. Никакие секреты не должны быть внутри архива. Для согласованного
релиза сохранить checksum исходников, lock, digest базовых образов и итоговый image ID.

Последовательность на сервере ТОЛЬКО после разрешения:

1. Read-only preflight: `uname -m` должен показать x86_64; `ss -H -lnt '( sport = :5433 or sport = :18080 )'`
   не должен показать слушателей. Проверить RAM/диск, DNS/HTTPS, Docker enabled,
   текущие процессы polling и наличие новой пустой БД. Никакого изменения UFW/WG.
2. `export KUDA_RELEASE_ID=<идентификатор-проверенного-комплекта>` (не секрет).
3. Проверить Compose без вывода env и собрать образ для этого релиза:

```sh
docker compose --env-file .env.production -f docker-compose.production.yml config --quiet
docker compose --env-file .env.production -f docker-compose.production.yml build bot
```

4. Сохранить предыдущий release image ID/tag и исходники. Запустить только PostgreSQL,
   выполнить restore-проверку, затем остановить Mac polling, сделать финальную копию
   и восстановить в новую пустую БД согласно разделу выше.
5. Запустить миграции из образа того же KUDA_RELEASE_ID. Проверить schema/data,
   выполнить `up -d --no-deps bot`. Не выполнять общий `up` до остановки Mac.
6. Проверить health/ready, логи polling_failure, Telegram UI, данные и работу WG.
   Новые порты не открывать в UFW: база и health слушают loopback, polling исходящий.

Compose-файл проверяет фиксированные непересекающиеся 5433/18080; реальные конфликты
на DigitalOcean без подключения проверить нельзя. Host networking не даёт отдельного
сетевого namespace; приложение должно слушать только 127.0.0.1. Публичный IP сервера
не использовать в DATABASE_URL_DOCKER. Никакого scale bot >1. Restart unless-stopped
не предотвращает второе polling в другом проекте или другом хосте: переключение
требует проверки остановки источника. Автоматической распределённой блокировки нет.

Откат релиза: сначала остановить единственный новый polling, проверить его завершение,
сохранить свежую серверную БД. Если миграции совместимы с предыдущим приложением,
выбрать прежний KUDA_RELEASE_ID и запустить только bot без build и без downgrade.
Не запускать миграции старой версии автоматически. При несовместимой схеме/данных
нужна отдельная проверенная стратегия обратного переноса без потери новых записей;
не откатывать БД на старый dump вслепую. Для возврата на Mac сначала сверить актуальную
базу и убедиться, что серверный polling завершён. WG/UFW/SSH в откате не меняются.

## Серверный подготовительный этап без polling

Новая PostgreSQL должна требовать SCRAM-пароль даже на loopback. Для новых volumes
production Compose задаёт POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256.
Unix socket внутри контейнера остаётся trust; доступа к нему из сети нет.
Пароль хранится только в защищённом .env.production. Для TCP psql/pg_restore
передавать PGPASSWORD из POSTGRES_PASSWORD внутри контейнера, не в аргументах CLI.
pg_isready не проверяет правильность пароля; отдельно проверять отказ без пароля
и успешный SELECT 1 с паролем. Уже созданному volume INITDB_ARGS настройки не меняет.

В подготовительном этапе БД kudavpn содержит пробную восстановленную копию.
Не восстанавливать финальный dump поверх неё: создать новую пустую БД
kudavpn_live_<идентификатор>, оставить пробную БД нетронутой и восстановить финальный
dump туда после согласованной остановки Mac polling. Затем указать новый database
в DATABASE_URL_DOCKER серверного env для bot/migrate, проверить схему и данные.
POSTGRES_DB можно оставить именем первоначальной служебной БД контейнера, чтобы
не пересоздавать PostgreSQL; pg_isready проверяет готовность сервера, не приложения.
Только после отдельного подтверждения запустить server bot из закреплённого образа.
Новый dump обязателен: ранее переданная копия не включает изменения после её snapshot.

## Предварительная проверка прав и образа миграций

Архив, распакованный с `umask 077`, может создать каталог `tests` с правами 700.
Контейнер приложения работает под UID 10001, поэтому bind mount такого каталога
нечитаем. Перед offline smoke из каталога релиза исправить только тестовые файлы:

```sh
find tests -type d -exec chmod 755 {} +
find tests -type f -name '*.py' -exec chmod 644 {} +
```

Каталог самого релиза сохранять закрытым (700), `.env.production`, резервные копии
и их отчёты — 600. Не менять права рекурсивно на всём релизе. Проверить отсутствие
символических ссылок в переданном каталоге тестов перед изменением прав.

Локальный остановленный контейнер `migrate` может ссылаться на старый image ID,
даже когда бот и база уже обновлены. Обновить только его, не затрагивая работающие
бот и PostgreSQL:

```sh
docker compose build migrate
docker compose up --no-start --no-deps --force-recreate migrate
docker compose run --rm --no-deps migrate alembic current
docker compose run --rm --no-deps migrate alembic check
```

`current` и `check` проверяют текущую ревизию и соответствие схемы моделям;
они не выполняют upgrade. Проверять image ID контейнера и наличие
`0002_active_screens` в образе, а не только имя тега. Offline smoke и эти проверки
нужно выполнить до остановки Mac-бота. Сам работающий Mac-бот сохраняет прежний
image ID до отдельно согласованного переключения.
