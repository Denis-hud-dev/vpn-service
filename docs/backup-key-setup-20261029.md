# Автоматическое резервирование: подготовка ключа владельца

29 октября2026, продолжение аудита [backup-disaster-recovery-20261029.md](backup-disaster-recovery-20261029.md), существующая ветка PR29.

## Текущий этап

Владелец разрешил бесплатные logical backups, шифрование, ограниченную передачу между существующими VPS, отдельную restore-lab и backup monitoring. **Удаление старых копий запрещено**: прежняя рекомендация14daily/8weekly/6monthly пока не разрешает pruning. Новые backup jobs, передачи и restore ещё НЕ установлены. Остановились перед созданием recovery key: парольная фраза должна быть известна владельцу, не агенту и не VPS.

На ограниченном preflight DO bot/PG/Xray running/healthy, StartedAt прежние; JH Xray/PG/test bot/site/Monitor active с прежними PID/start times. Свободно около72.32GiB/14.82GiB соответственно. Оба Linux x86_64, Python3.12.3/systemd255, /run tmpfs. Docker доступен на DO, native pg_dump/pg_restore на JH. age отсутствует на VPS; gpg и systemd-creds доступны. Никакие пакеты/службы/настройки серверов на этом этапе не менялись.

## Защита ключа

Архивы планируются в стандартном формате **age**, native X25519 recipient, authenticated encryption. Для обоих источников допустим один recovery recipient владельца; каждое шифрование age использует независимый случайный материал. Приватный recovery identity на VPS не передаётся.

Локальный helper использует официальный age-keygen; приватный identity существует в памяти и записывается только после обёртки **Scrypt(N=131072,r=8,p=1,random salt16B) + Fernet** из cryptography. Fernet обеспечивает аутентификацию/конфиденциальность; это формат хранения маленького ключа, а не новый формат PostgreSQL-архивов. Salt/параметры/время не являются секретом; пароль и plaintext identity в файлах/логах/аргументах/env отсутствуют. Стоимость KDF фиксирована: подмена файла не может навязать неограниченную память. Core dumps отключены в launcher и интерактивном main. Строгая гарантия стирания Python-объектов/OS swap не заявляется.

Helper проверяет round-trip **записанного** encrypted key и соответствие public recipient. Повторный запуск не создаёт другой ключ и не переписывает старый. Ошибочный пароль/повреждение/неверные права/симлинки/каталог Git/изменённый executable приводят к отказу. Hidden-input fallback с предупреждением об echo запрещён. Режимы: каталог0700, private/metadata files0600, launcher/script/tools0700.

Mac-инструменты age1.3.2 взяты из официального release. Archive SHA256 из GitHub release API:
`e2020b073c44f692685a24d6abc378817eb81ffaaf49fd0531ef8565f767f2f5`.
Файлы проверяются перед запуском:
- age: `4012dfc2725883beafb710894af4f599b7a94f8c8e0f51f02cc96ab8df33915e`
- age-keygen: `c16e229245123d0ad27442317461d63915416cad0294395cd19ca93feb3211ea`

## Действие владельца

В собственном Mac Terminal запустить:

```sh
/Users/gr/Documents/KudaVPN-private/backups/setup-backup-key.command
```

Уникальная парольная фраза минимум16 символов (лучше несколько случайных слов) вводится дважды скрыто. Сохранить её в менеджере паролей. После сообщения `Recovery key encrypted and verified` скопировать **зашифрованный** `/Users/gr/Documents/KudaVPN-private/backups/recovery-key.encrypted.json` на независимый носитель или в защищённое хранилище. Пароль/файл не присылать в чат, GitHub или поддержку.

Без encrypted file **и** парольной фразы архивы восстановить нельзя. Сохранить также helper, описание формата и официальные age tools/manifest; при потере Mac нужно иметь возможность заново получить Python+cryptography и публичный код восстановления. Ничего не удалять и не ротировать самостоятельно после начала резервирования.

Локальные результаты helper:
- recovery-key.encrypted.json — зашифрованный private identity, остаётся у владельца;
- recipient.txt — только открытый ключ для будущих backup jobs;
- key-ready.json — public recipient, SHA encrypted file, verified и время; не содержит private key;
- .setup.lock — защита от одновременных запусков.

К моменту подготовки документации настоящий ключ ещё НЕ создан. Агент может проверить наличие/права/ready digest и public recipient, не расшифровывая ключ. Для будущей реальной restore-проверки потребуется отдельный скрытый ввод владельца: нельзя тайно сохранять пароль в агенте ради автономного decrypt.

Launcher использует уже установленный bundled Python3.12 с cryptography50.0.2. Helper не требует сети и ничего не делает с серверами. Если runtime переехал, изменить только локальный launcher после проверки доступного Python+cryptography; encrypted file не ротировать.

## Точная точка продолжения после «Готово»

1. Проверить key-ready/recipient и права, без чтения private identity/пароля. Подтверждение владельца о сохранении recovery material необходимо до activation.
2. Снять безопасный baseline PID/StartedAt/config digests и свободного места; не повторять полный аудит. Подготовить rollback только новых backup units/files/accounts, не трогая текущие конфиги и прежние архивы.
3. Развернуть проверенные age tools в отдельный каталог без upgrade/restart рабочих пакетов. Снять consistent custom dumps **DO live DSN kudavpn_live_20261010152329/5433**, JH canonical0008 и website0009; не выбирать начальный POSTGRES_DB и не создавать второй источник подписок. Globals/role map и конфигурации/SQLite backup API — отдельные encrypted artifacts. Секретные потоки держать в RAM/tmpfs и сразу шифровать, не писать plaintext backup на обычный диск.
4. Реализовать lock, bounded timeout/retry, проверку выхода всех звеньев pipeline, atomic promotion, дисковые пороги/квоты и уникальные имена без перезаписи. **Никакого удаления старых копий и автоматического pruning.** Отсутствие места должно приводить к ошибке и уведомлению, а не удалению good archive.
5. Для cross-VPS только новые отдельные SSH identities, host-key pinning и forced-command receiver на существующем22. Никаких shell/SFTP/forwarding/PTY. Получатель не должен разрешать overwrite/delete/list-secrets. Предпочтительно unprivileged inbox + root-owned sealed vault; promoter копирует ciphertext в новый root-owned inode (не оставлять writable FD загрузчика), затем проверяет SHA/размер и выдаёт ACK. Старый vault недоступен для удаления uploader. Ограничить размер/квоту/время/пути/симлинки; подтвердить negative tests. Upload credential at rest — systemd encrypted credential; recovery decrypt key на VPS не сохранять.
6. Добавить новые daily oneshot/timers с Persistent, flock и ограничениями нагрузки. Отдельный backup health timer может проверять per-DB last-success/encryption/offsite ACK/integrity/free space; не изменять существующий Monitor/его расписание без согласования. Если нужны изменения его production-кода или новые alert secrets — сначала спросить. Без свежих подтверждений статус «нет данных», не зелёный.
7. Провести свежий restore на DO в отдельном PostgreSQL16 контейнере: существующий pinned image, network none, без published ports, отдельный tmpfs PGDATA, лимиты CPU/RAM/PIDs. Не использовать production volume. Расшифровка ключа — на Mac со скрытым вводом владельца; в VPS передавать только поток dump в lab, не recovery identity. Проверить manifest/schema/counts/constraints/sequences/row hashes, user/payment/subscription references, grant idempotency и сохранение UUID/token. Не запускать bot polling, API writes, платежи или уведомления клиентам.
8. Зафиксировать UTC runIDs, encrypted paths/bytes, offsite ACK, restore results, next daily runs, мониторинг и неизменность PID/configs рабочих служб. Сделать encrypted третью копию на Mac, не рядом с единственной копией ключа. Проверка после реальной аварии всё равно требует согласованного cutover, не автоматического отката живой БД.

Две VPS не дают абсолютной изоляции от одновременного компрометации root; offline owner copy остаётся необходимым дополнительным уровнем. Планируемые операции не требуют платных услуг и новых публичных портов.

## Проверки текущего этапа

16 локальных тестов PASS, включая настоящий age encrypt/decrypt на синтетическом payload и отказ после повреждения ciphertext; неверный пароль, KDF tampering, permissions/symlinks/Git guard, отсутствие plaintext на диске, повторный запуск без ротации, no-overwrite и binary checksum. После readback/core-dump hardening повторены только эти16 тестов. py_compile helper/source/tests и sh -n launcher PASS. Ключ владельца и рабочие дампы для тестов не использовались. Это **не** подтверждение ежедневного расписания, offsite или восстановления настоящей БД.

Код: scripts/prepare_backup_recovery_key.py, tests/test_backup_recovery_key.py. Официальный CLI test в общем CI требует KUDA_TEST_AGE_DIR; без него один этот тест явно skipped, остальные остаются автоматическими.

Источники: [age1.3.2](https://github.com/FiloSottile/age/releases/tag/v1.3.2), [age CLI](https://github.com/FiloSottile/age/blob/v1.3.2/doc/age.1.ronn), [age-keygen](https://github.com/FiloSottile/age/blob/v1.3.2/doc/age-keygen.1.ronn), [Fernet](https://cryptography.io/en/latest/fernet/), [Scrypt](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/#scrypt).
