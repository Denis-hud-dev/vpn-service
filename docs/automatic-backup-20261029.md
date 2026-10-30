# KudaVPN: бесплатное автоматическое резервирование и restore-lab

Установлено29 октября2026; приёмка завершена30 октября05:45 UTC. Ветка PR29. Продолжение [аудита](backup-disaster-recovery-20261029.md) и [церемонии ключа](backup-key-setup-20261029.md).

## Итоговая приёмка — 30 октября2026, 05:45 UTC

**Реальное восстановление завершилось успешно30 октября в05:42:07 UTC.** Локальный отчёт владельца и отдельный server-side receipt совпали:
- live / revision0002:7 таблиц PASS;
- pilot / revision0008:13 таблиц PASS;
- website / revision0009:19 таблиц PASS;
- совпали counts и row hashes всех таблиц, defaults/columns/indexes, CHECK/validation flags, FK/owner references и уникальность provider payment IDs;
- role/run artifacts расшифрованы; конфигурации DO125/JH1104 файлов authenticated и required files present;
- label lab2cb35850813e удалён, иных restore labs нет; production containers/PIDs/configs/TCP listeners неизменны.

Проверялись именно закреплённые29-октябрьские runs из Mac index. Более новые ночные30-октябрьские версии прошли автоматические ciphertext/offsite проверки, но не объявляются отдельно расшифрованными/восстановленными. [Безопасный acceptance JSON](backup-acceptance-20261030.json) не содержит строк, user IDs, row hashes, ключей или subscription URLs.

Последний nightly: DO03:19:07–03:20:07 UTC (66 639 384B), JH03:49:35–03:50:59 UTC (21 830 088B), обаstate=ok. Integrity наобоих05:37 ok/12files; health05:39 errors={}. Next31 октября03:15:54 UTC и03:49:22 UTC, daily jitter может пересчитаться. Наэтом задача завершена; дальнейшие реальные restore/dumps для доказательства уже полученного результата не нужны.

Текущее место local+vault: DO332 035 112B (~317MiB), JH353 638 769B (~337MiB). Свободно71.95/14.46GiB. Стоимость дополнительных облачных услуг0; old versions не удалялись. Основной оставшийся организационный шаг — заранее разрешить retention или пересмотр quotas доих достижения примерно через4недели.


## Что установлено

Только новые компоненты `kudavpn-backup*`. Production bot/PostgreSQL/Xray, тестовые bot/feed/site, Monitor, рабочие env/платёжные настройки и сетевые listeners не перезапускались и не менялись. Добавлены отдельные OS SSH-аккаунты, файлы backup и systemd units. Ни покупок, ни новых VPS, ни paid DO backups/snapshots, ни публичных портов.

На обоих существующих VPS:
- `/usr/local/lib/kudavpn-backup/backup.py`, age1.3.2 и вспомогательный код;
- `/etc/kudavpn-backup/config.json`, pinned known_hosts (root only);
- encrypted upload credential `/etc/credstore.encrypted/kudavpn-backup-upload`;
- `/var/lib/kudavpn-backup/local/`: собственные encrypted artifacts, root0700/files0600;
- `/var/lib/kudavpn-backup/vault/<peer>/`: sealed копии другого сервера, root:receiver0750/files0440;
- `receipts/<peer>/`: root-owned проверенные ACK;
- `inbox/<peer>/`: ограниченный промежуточный приём; после sealing удаляются только новые transit duplicates, **не архивы**.

Третья encrypted копия на Mac: `/Users/gr/Documents/KudaVPN-private/backups/copies/`. Старые файлы/индексы и прежние ручные backups сохранены. Ключ владельца остаётся encrypted на Mac, в Git и VPS его нет.

## Реальные комплекты

Первый полный принятый комплект с PostgreSQL-конфигурациями, сохранённый также на Mac:

| Источник | Snapshot/job start, UTC29 октября | Run | Артефактов / ciphertext |
|---|---|---|---|
| DigitalOcean | 20:43:47 | 20261029T204347Z-3ceff808 | 5 / 66 629 144 B |
| JustHost | 20:43:48 | 20261029T204348Z-cd3d3e00 | 7 / 21 830 086 B |

Внутри каждого run: custom database dump + encrypted manifest каждой БД; globals/roles SQL; configuration tar; run manifest. Manifest содержит revision, counts и row hashes из того же exported read-only snapshot, столбцы/defaults, constraints/validation/indexes, PostgreSQL settings. Роли и пароли ролей находятся только в encrypted globals, не в журналах.

**Первый самостоятельный ночной запуск подтверждён30 октября:**
- DO: start03:19:07, завершён03:20:07 UTC, run `20261030T031907Z-c3ca4879`, status ok/offsite/checksums.
- JH: start03:49:35, завершён03:50:59 UTC, run `20261030T034935Z-fda66c92`, status ok/offsite/checksums.
- Integrity на обоих хостах ok в05:22 UTC, по12 последних local/vault files. Backup-health JH в05:24 — errors={}.
- Защищённые working units/containers/config hashes/TCP addresses совпадают с predeployment baseline.

Mac-index пока указывает на проверяемые файлы29 октября, а не автоматически на самый новый nightly run. Это намеренно фиксированная выборка для повторяемой restore-проверки; третья копия на Mac обновляется оператором, не зависит от постоянной работы Mac для ежедневного server→server backup.

## Какие данные защищены

- DO: **фактическая** `kudavpn_live_20261010152329` / PostgreSQL16.15 / локальный5433 / revision0002,7 таблиц. Начальный POSTGRES_DB не подменяет live DSN.
- JH: каноническая `kudavpn_telegram_pilot` / revision0008,13 таблиц: подписки, платежи, UUID/node_access, HTTPS tokens, история/уведомления и связи.
- JH: `kudavpn_website_test` / revision0009,19 таблиц: web identities/sessions/flows, review и НПД-реестр. Не стала вторым источником активных подписок.
- Globals и role map обоих PG-кластеров; PostgreSQL settings. DO из PGDATA читаются **только конфигурационные** postgresql.conf/auto.conf, pg_hba/ident и PG_VERSION, не live data files. JH: /etc/postgresql*, фактические PG TLS cert/key.
- Прежние приложения, env, systemd/drop-ins, SSH config/host keys, Xray/REALITY, TLS/OIDC/feed/payment configs, UID/GID inventory и deployment metadata.
- SQLite onboarding/leases/checkout/Monitor state — snapshot через SQLite backup API в RAM. WAL/SHM/journal не копируются рядом с самостоятельным SQLite snapshot. Heartbeat/read model не выдаётся за каноническую БД.
- Реальные deployed source releases и DO Docker inspect с runtime settings зашифрованы. Полного docker image save пока нет: после катастрофы потребуется rebuild из сохранённого source/locks либо доступ к соответствующему image. Это ограничение RTO.

## Расписание и ограничения нагрузки

- Daily DO:03:15 UTC + случайная задержка до5мин.
- Daily JH:03:45 UTC + до5мин.
- Следующие значения на05:24 UTC30 октября: DO31 октября03:15:54, JH31 октября03:49:22. Важны окна; systemd может пересчитать jitter после daemon-reload.
- timers enabled/active, Persistent=true: наверстать пропущенное после загрузки.
- After Docker/native PostgreSQL (без Requires/Wants/start/restart рабочих служб); read-only pg_isready ожидание до60сек.
- flock исключает одновременные backup/retry; три ограниченных попытки upload, затем hourly retry без повторного dump.
- Snapshot транзакции закрываются **до сетевой передачи**.
- CPUQuota25%, Nice15, IO idle/weight10, MemoryMax384M, timeout20мин, NoNewPrivileges/ProtectSystem/PrivateTmp. Source PostgreSQL не перезапускается, pg_dump lock wait ограничен5сек.
- Новые sealer jobs раз в10сек, без path-event bursts. Systemd rate limits сохранены.

Логический backup согласован внутри одной БД, но не является атомарным snapshot всех БД/SQLite сразу. При аварийном возврате нужно финансовое reconciliation, не повторять списания и не восстанавливать устаревшую БД поверх новых платежей.

## Передача и шифрование

Данные → age → файл с уникальным именем .partial → fsync/атомарная публикация. Plaintext dump/config tar не записывается на обычный диск. age использует только public recipient владельца, encryption output checksums сверяются на стороне получателя.

Linux age1.3.2 скачан из официального release; archive SHA256 `cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10` проверен до извлечения/запуска. Worker сверяет executable SHA перед шифрованием.

На JH отдельный `kuda-backup-do`, на DO `kuda-backup-jh`: locked password, root-owned authorized_keys, from=IP другого VPS, restrict, forced command. Нет произвольного shell/SFTP/forwarding/PTY через эти ключи. Receiver принимает только bounded JSON + age ciphertext, ограниченные имена и размер≤256MiB. Не поддерживает delete/overwrite.

Unprivileged inbox не владеет sealed vault. Root sealer создаёт **новый inode** и проверяет ciphertext SHA/размер; сохранённый writable FD загрузчика не позволяет изменить sealed файл. ACK публикуется после fsync. Повторная передача того же файла возвращает прежний ACK, а другое содержимое с тем же именем отвергается. Живые проверки подтвердили: receiver UID не может писать в vault и уже принятые архивы.

Upload SSH private identities отдельные для каждого направления, генерируются на /run tmpfs и хранятся at rest как systemd encrypted credentials. Runtime plaintext находится в защищённом credential tmpfs; приватного **recovery** ключа там нет. Компрометация source root даёт доступ к его upload identity, но этот канал не позволяет удалить старые remote vault files. Два скомпрометированных root/потеря ключа остаются риском; независимая offline-копия владельца обязательна.

## Контроль и уведомления

- verify timer15мин на обоих VPS: executable checksum, последние local/vault ciphertext SHA/size, свободное место.
- health timer15мин на JH: свежесть по каждой стороне (26ч), offsite ACK/encryption/checksums, integrity heartbeat≤30мин, текущий job/error/stuck>21мин и диск.
- Нормальный running job при свежей предыдущей копии не вызывает ложное аварийное сообщение.
- Ошибки и recovery — только подтверждённому owner_id существующего Monitor bot. Токен читается из прежнего защищённого файла, не копируется; getUpdates не вызывается.
- Повторные ошибки подавляются; повтор напоминания не чаще4ч. Неопределённый результат отправки отмечается unconfirmed; не считается доставкой.
- Сам Monitor, его worker/config/таймер4-часовых отчётов не менялись. Backup-health — отдельный наблюдатель. Синтетические real-outage уведомления не отправлялись; доставка/дедуп/recovery проверены mocks.
- Диагностика: status.json, integrity.json, health-state.json и journalctl -u kudavpn-backup.service / -verify.service / -health.service. Журналы содержат коды/статусы, не SQL, строки, ключи или URLs.

Резервирование не умеет расшифровывать owner archives на VPS. Ежедневная integrity — ciphertext check + успешный trusted encrypt/ACK, не ежедневный полный restore. Полную AEAD/decrypt/restore-проверку выполняет владелец локальным helper.

## Восстановление и текущий результат

Локальная команда владельца:

```sh
/Users/gr/Documents/KudaVPN-private/backups/verify-backup-restore.command
```

Пароль только в скрытом запросе Mac. Native age identity передаётся age через локальный file descriptor, не argv/env/диск/VPS. Ciphertext hash проверяется до расшифровки. Plaintext flows только в отдельный PG16 контейнер:
- network none, без public ports и production volumes;
- PGDATA/tmpfs, read-only rootfs, no-new-privileges;
-512MiB RAM,0.5CPU,128PIDs;
- Docker log-driver none и SQL logging выключены для lab;
- rollback/удаление только валидированного labelled lab-container; TTL45мин с отдельным cleanup timer на DO при обрыве Mac/SSH.
- Проверяются revision, все таблицы/counts/row hashes, defaults/constraints/indexes, ссылки user→subscription/payment и отсутствие duplicate provider IDs. После проверки — удаление lab и подтверждение неизменности рабочих containers.
- Globals/конфигурационные архивы проверяются на decrypt/authentication и наличие обязательных файлов без извлечения секретов на диск.

Синтетический полный pipeline трёх DB в такой изоляции PASS; fixture lab удалён. Это не подменяет реальную проверку.

**Реальная первая попытка30 октября05:17–05:18 UTC:**
- live восстановлен и проверен PASS;
- pilot был импортирован; старый helper не сохранил отдельные результаты всех его дальнейших сравнений. Подтверждённый полный PASS первого отчёта относится только к live;
- на схеме pilot доказан дефект строгого текстового сравнения CHECK premium_notice_kind, достаточный для отказа прежней проверки. Полный результат строк pilot затем подтверждён успешным повтором, см. приёмку выше. Lab удалён, рабочие containers неизменны. Все12 encrypted files Mac имели правильные SHA/размеры, OOM/аварии Docker не было.
- На schema-only копии без user rows воспроизведено1 различие для pilot и8 для website: только deparser representation CHECK clauses после dump/restore.
- Исправление: никакого удаления casts/regex-normalization. В writable транзакции **только восстановленной lab** PostgreSQL повторно разбирает исходный CHECK во временное ограничение и сравнивает полную canonical clause. Транзакция откатывается. Имена/validation flags, столбцы/defaults/indexes и все не-CHECK constraints остаются строгими.
- Обе реальные схемы теперь проходят backend canonical comparison. Намеренно другое CHECK(false) отвергнуто даже на пустых таблицах; нельзя подменить проверку семантики фактом прохождения существующих строк.
- Добавлены безопасные stage/error codes и сохранение старых локальных отчётов.
- Повтор полного реального decrypt/restore впоследствии завершился PASS30 октября05:42:07UTC; см. итоговую приёмку выше. Пароль/ключ в чат не передавались.

Для настоящей аварии эта команда — только rehearsal, не автоматический cutover. Использовать [DR-процедуру](backup-disaster-recovery-20261029.md): восстановить globals/роли в новом чистом PG16-кластере с уникальным временным bootstrap admin (чтобы CREATE ROLE не конфликтовал с уже существующим исходным admin), затем новую БД, exact deployed app/env/UUID/feed secrets, проверить offline и лишь после согласования переключать единственный bot/сеть/DNS. Не запускать старый controller9443 и не откатывать боевые платежи stale dump. Если DO потерян, текущий lab-script, использующий его existing image, сам по себе не является emergency installer нового VPS.

## Ёмкость, стоимость, оставшиеся ограничения

На20:47 UTC29 октября новые local/vault ciphertext занимали примерно244MB на DO и265MB на JH, включая сохранённые первые попытки. Mac два полных принятых поколения≈177MB. Ночные новые версии добавляются, старые не удаляются. Актуальный размер смотреть du/status, числа не являются лимитом облачного тарифа.

Есть quota2GiB отдельно для local и peer vault и reserve≥2GiB/20% диска. Это защита от заполнения VPS, не удаление. Конфигурационные tar сейчас несжатые (~64/21MiB на run): при таком росте local/vault DO-данных достигнут quota примерно за месяц. Требуется заранее согласовать retention/ёмкость; до этого **автоматического pruning нет**. При достижении quota/disk threshold backup останавливается с ошибкой, старые версии сохраняются и владелец уведомляется.

Дополнительных платных услуг **$0/месяц** при нынешнем объёме и включённом межсерверном трафике. Старый paid DO snapshot не менялся. Ежедневный RPO примерно24ч+jitter/длительность; полноценного WAL/PITR нет. Полный bot/VPN cold-start RTO ещё не измерен, IP/DNS/provider hostname failover не автоматизирован. Нельзя обещать продолжение Happ по тому же адресу после потери IP без восстановления DNS/адреса.

## Откат только backup-подсистемы

```sh
sh /usr/local/lib/kudavpn-backup/pause-backups.sh
```

Останавливает/выключает **только новые backup timers**, сохраняет все архивы/credentials и не останавливает bot/PG/Xray/Monitor. Уже запущенный read-only backup может завершиться. Статусы/старые units и manual copies не удаляются; схема БД не понижается и данные не восстанавливаются автоматически. Для отзыва нового upload key отдельно root может закрыть его authorized_keys, не затрагивая admin SSH.

## Проверки и provenance

50 профильных тестов PASS (16 owner-key,27 pipeline/security/lab,7 owner-helper), реальный age roundtrip/corruption, backend schema-only positive/negative regression, pg schema fixture restore, live receiver permissions, nightly timers, normalized working baseline. py_compile/sh-n/diff hygiene PASS. Общий CI проверяется после публикации, повторный полный локальный набор приложения не запускался.

Прежние setup failures не скрыты: raw ss hash включал изменяемые queue/fd — guard заменён на точное сравнение адресов/портов плюс прежние PID/config checks; новый path trigger упирался в start-limit — заменён на bounded timer10сек без отключения rate limit. Первые encrypted attempts сохранены. Эти изменения касались только backup-подсистемы.

Код: deploy/backup/{backup.py,provision.py,restore_lab.py,pause-backups.sh}, scripts/verify_backup_restore_owner.py. Provision payload состоит из public recipient, уже проверенных SSH host keys, runtime paths/revisions и исходников модулей; не содержит DB паролей. Для повторного развёртывания на другой машине нужны новый план/host-key подтверждение, нельзя просто применять сегодняшние IP/DSN к production без проверки.
