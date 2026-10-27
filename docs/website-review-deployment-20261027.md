# Закрытый HTTPS-стенд сайта — 26–27 октября 2026

[Draft PR №29](https://github.com/Denis-hud-dev/vpn-service/pull/29), runtime `b7c636db94b9db7a48d577f97d81cbcd53f34277`, отдельная ветка `codex/standalone-site-v1-20261026`. Сайт развёрнут только для владельца:

**https://r1276248.s2578.vm.mydomain.zone:20443/**

Это закрытая проверка авторизации и существующей подписки. Реальные продажи/платежи, новые UUID, переключение production и миграция DigitalOcean не выполнялись. Telegram-боты, Xray, Monitor и исходная pilot-БД не обновлялись/не перезапускались.

## Установка и изоляция

- Новые `kudavpn-website-review.service` и `kudavpn-website-export.timer` активны и включены на boot. Export service — ограниченный oneshot; timer запускает его спустя30 секунд после окончания, одновременно второй экземпляр не запускается. Snapshot не старше120 секунд; иначе отказ503.
- Отдельные root-owned release/venv в `/opt/kudavpn-website-review`; current указывает на `release-b7c636d`. Существующий venv не изменялся. User/group сайта `kudavpn_website_test`, no shell/sudo/capabilities, ProtectSystem=strict/NoNewPrivileges/PrivateTmp/ProtectHome и закрытый доступ к исходным конфигам. Сайт и exporter ограничены320MiB/50% CPU; exporter TimeoutStart60s. Реальное чтение без квоты9.77s, peak203660KiB; сайт после запуска использовал около180MiB. Нагрузка не является коммерческим capacity test.
- Новая PostgreSQL `kudavpn_website_test`, passwordless local peer, отдельная роль без superuser/createDB/createRole/replication/bypassRLS. Миграция0009 только здесь. Приложению выданы SELECT/INSERT/DELETE только flows/review_sessions и SELECT revision. Даже пустые canonical user/subscription/payment таблицы новой БД не доступны на запись; в них0 строк. У роли нет SELECT/INSERT/UPDATE/DELETE в исходном pilot. Source schema0008 не мигрировалась.
- Source читает фиксированный root-exporter в REPEATABLE READ, READ ONLY. Сайт не получает source DB URL, bot token, HMAC key, agent/SSH/provider credentials. Snapshot root:private-group0640, directory0750; website не может менять snapshot, код или config.
- OIDC взят из согласованного Mac файла0600, каталог0700 вне Git; source на VPS `/etc/kudavpn-website-review`0700, файлы0600. Загрузка только SSH stdin с pinned host key, без аргументов/вывода. Upload staging удалён. Client ID соответствует существующему test bot. Runtime LoadCredential — root-owned read-only mount, файл0440/каталог0550 и ACL только root/UID службы. Код проверяет exact directory/names, ownership, modes, read-only flag и эксклюзивный ACL; права секретов не ослаблялись.
- TLS использует отдельную приватную копию уже доверенного сертификата/ключа того же имени; новые ACME-аккаунты/сертификаты/прокси/firewall правила не создавались. Текущий сертификат до18.01.2027. Обновление копии перед истечением — отдельная обязанность review-стенда; существующий renew pipeline не менялся.

## Что проверено

- CI runtime b7c636d успешен: [PR run](https://github.com/Denis-hud-dev/vpn-service/actions/runs/40465856441), [branch run](https://github.com/Denis-hud-dev/vpn-service/actions/runs/40465850212). Локально49 профильных тестов passed; полный прежний набор локально повторно не запускался.
- Mac HTTPS с проверкой имени/цепочки без `-k`: страницы `/`, `/account`, `/connect`, data API, документы, private assets, `/ready` снаружи, fixture-login и попытки URL-ID без cookie возвращают401. Ответы no-store/private + no-referrer, персональной feed-ссылки в них нет. Анонимно только минимальный вход и его публичные logo/CSS.
- `/auth/login` даёт303 на официальный oauth.telegram.org/auth, точный redirect `https://r1276248.s2578.vm.mydomain.zone:20443/auth/callback`, только openid/profile, S256, __Host/HttpOnly/Secure/SameSite flow-cookie. Настоящая форма Telegram показала выбранного бота; реальный callback вернулся с code/state на20443. Таким образом сам порт/callback дошёл через Telegram, но успешная сессия ещё требует отдельного подтверждения ниже. Административный список Trusted Origins напрямую не считывался; локальные значения точные, JS widget не используется.
- Источник: существующая active подписка,3 собственных test payment записи и прежний feed. HTTPS feed вернул200, один VLESS узел10443 и no-store. UUID/URL/имя/ID не выводились и не переносились в Git. Выбор199/499/1790 в review — только selection_only, без заказа, денег или изменения срока.
- Исходные PID/listeners и hash реальных Xray-конфигураций443/8443/9443/10443, bot/feed/checkout остаются идентичны baseline. После неготовности нового компонента несколько раз автоматически остановлены только новые units; действующие службы не затронуты.

## Исправленные проблемы подготовки

- umask077 закрыл новые runtime-каталоги от service UID/postgres. Исправление разрешает чтение только public code/venv, не меняет секреты/внешние symlink targets; есть регрессионный тест.
- Первичный лимит20s/25% CPU был ниже измеренного cold start; увеличены только лимиты новых units, сохранены hard caps и readiness/TLS проверки.
- Строгий file checker не учитывал запечатанный systemd ACL layout; теперь проверяет его структуру без chmod или разрешения group-readable файлов вне systemd.
- Общий begin() пытался чистить legacy web_sessions, на которую isolated role не имеет прав. Cleanup теперь выбирает ReviewSession; grants не расширялись. Тест воспроизводит DB-deny на любых legacy/canonical таблицах.
- HTTP reader мог разобрать неполный JSON после первого TCP-фрагмента. Теперь читает до EOF с общим лимитом64KiB. Тест потокового ответа это воспроизводит. Ответ Telegram200 с OAuth error классифицируется корректно; operator log содержит только фиксированные outcome-категории, без body/claim/token/code/ID/URL.

## Живой вход — точка продолжения

Первое подтверждение владельца вернуло callback401 и не создало ReviewSession. Правильные cookie/state были приняты: flow атомарно использован. Старый код не повторяли. Часы VPS совпадают с Date Telegram; один negative grant probe вернул invalid_grant без токенов. Этот тест не является успешным входом и не доказывает полную live-конфигурацию сам по себе. Причина исходного отказа без диагностики не объявляется установленной.

После установки b7c636d создан **один новый нормальный flow**; выбран уже авторизованный аккаунт, Telegram отправил владельцу новое уведомление подтверждения. Владелец должен подтвердить его. Дальше:

1. Проверить автоматический возврат `/account`, display/status/expiry/remaining/history и кнопку `/connect`, не выводя приватные данные/QR.
2. В auth-БД проверить наличие только owner sessions, canonical rows по-прежнему0. Если отказ — читать только whitelist `website_auth outcome=...` из журнала нового website service; не выгружать callback URLs/токены и не повторять использованный код.
3. По точной категории установить причину (provider error/signing algorithm/issuer/audience/timing/owner mismatch). Не ослаблять проверки по предположению; при необходимости дать владельцу одно конкретное действие в BotFather/Telegram.
4. Проверить logout/replay и сохранение подписки, затем обновить этот отчёт. Успешный полный OIDC/Happ E2E пока **не заявлен**. Публикация для других пользователей и платежи не разрешены.

## Backup и откат

Закрытый backup `/var/backups/kudavpn-website-review/20261026`: исходный baseline, rollback, восстановимый dump только новой auth-БД, prepared private config, предыдущие targets/new unit versions и закрытая диагностика установки. Dump проверен pg_restore --list и --file=/dev/null. Source DB не копировалась для второго controller и не восстанавливалась.

`bash /var/backups/kudavpn-website-review/20261026/rollback.sh` останавливает/disable только website-review/export timer/service, закрывает20443 и проверяет protected baseline. Сохраняет изолированную auth-БД/config/release для диагностики. Не трогает source DB/подписки/порты/ботов/firewall/Monitor/DO. Откат реально выполнялся при сбоях новых компонентов; текущий runtime после smoke активен.

Скриншот `docs/screenshots/website-live-closed.png` — только анонимная страница без личных данных. OAuth/QR/персональные страницы не сохранялись в screenshots/Git. Развёртывание относится только к этому origin; кнопки ботов не изменялись.

## Блокирующее действие владельца при остановке

Mac заблокирован; Computer Use не может продолжить UI/GitHub Desktop без ручной разблокировки. Финальное read-only чтение: website service/export timer/Monitor active, protected baseline идентичен, owner/foreign sessions пока0, новых безопасных auth outcomes нет — подтверждение нового уведомления ещё ожидается. Runtime b7c636d уже опубликован и CI успешен; этот финальный документационный commit пока только локальный. После разблокировки проверить актуальную форму/уведомление Telegram, при истечении10-минутного flow начать новый обычный вход с корня сайта, не повторять старый callback/code. Затем проверить owner session/кабинет/выход и отправить только документационный commit существующей ветки. Не повторять установку, инфраструктурный аудит, миграции или диагностику SSH.
