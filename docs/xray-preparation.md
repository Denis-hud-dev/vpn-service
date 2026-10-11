# KudaVPN: план Xray / VLESS / REALITY

Статус: только аудит и проектирование. Xray не установлен и не запущен;
правила firewall, SSH, WireGuard, маршруты, production-код и базы не изменены.
Установка и открытие TCP 443 требуют отдельного подтверждения владельца.

## Фактический аудит 2026-10-10

209.38.196.186: Ubuntu 24.04.5 LTS, x86_64; Docker 29.9.0, Compose 5.6.0.
Бот и PostgreSQL healthy. SSH, Docker, wg-quick@wg0 active. UFW active:
разрешены 22/tcp, 51820/udp и существующий forwarding wg0 ↔ eth0.
TCP listeners: SSH 22, DNS на loopback 53, PostgreSQL 127.0.0.1:5433,
бот 127.0.0.1:18080. TCP 443, 8443 и 10085 не заняты на момент проверки.
Интерфейсы eth0 (публичный), eth1 (VPC), wg0, lo; docker0 DOWN.
Маршруты читаются, не изменяются. Доступная память около 3.1 GiB,
диск около 74 GiB, swap отсутствует. Это запас для пилота, не оценка ёмкости VPN.
DigitalOcean Cloud Firewall не проверен через API/панель: его состояние нужно
проверить отдельно перед разрешением внешнего подключения.

## Документация и совместимость

- https://xtls.github.io/en/config/inbounds/vless.html
- https://xtls.github.io/en/config/transports/reality.html
- https://xtls.github.io/en/config/api.html
- https://github.com/XTLS/REALITY/blob/main/README.en.md
- https://github.com/XTLS/Xray-core/releases/latest
- https://www.happ.su/main
- https://www.happ.su/main/dev-docs/examples-of-links-and-parameters.md

Happ официально использует Xray, поддерживает VLESS REALITY на iOS/Android,
импорт vless:// из буфера/QR и подписки. Это не заменяет проверку конкретных
версий приложений на реальных телефонах. GitHub latest при аудите указывает
v26.3.27: перед установкой повторно проверить официальный stable release,
закрепить version/digest и проверить SHA256 дистрибутива linux-amd64.
Документация main может опережать release: проверить JSON выбранным бинарником.

## Архитектура узла

Отдельный Compose-проект `kudavpn-xray`, без depends_on существующего бота/БД.
Контейнер с host networking: без нового bridge, NAT, маршрутов и TUN.
Публичный listener 209.38.196.186:443/TCP; API 127.0.0.1:10085,
не публиковать API, статистику и PostgreSQL. 443 сейчас свободен;
будущий HTTPS-сервис на том же IP:443 создаст конфликт — потребуется отдельно
спроектированное разделение или другой адрес. Не открывать UDP 443:
клиентский UDP может перевозиться внутри прокси-транспорта.

Непривилегированный UID, read-only filesystem, конфигурация read-only mount,
cap_drop ALL и только NET_BIND_SERVICE для 443, no-new-privileges,
без Docker socket, NET_ADMIN, privileged и /dev/net/tun.
Restart unless-stopped; лимиты/ротация логов; первоначальный бюджет памяти
512 MiB и CPU 1, затем замеры под нагрузкой. Watchdog должен проверять также
работоспособность подключения, а не только процесс; Docker healthcheck сам
по себе unhealthy-контейнер не перезапускает.

## Параметры подключения

- inbound VLESS, transport RAW/TCP, security REALITY, flow xtls-rprx-vision;
- персональный случайный UUIDv4 на каждое устройство, opaque user label;
- VLESS decryption none: шифрование транспорта обеспечивает REALITY;
- show false, xver 0; X25519 privateKey генерировать только на сервере;
- непустой случайный shortId из 8 bytes (16 hex), без пустого shortId;
- serverNames с конкретным проверенным SNI, target=SNI:443;
- выбрать target после проверки с сервера TLS 1.3, ALPN h2, сертификата,
  задержки и стабильности; предпочтительно подходящий endpoint того же ASN.
  Не направлять target на собственный listener (петля).
- клиент fingerprint chrome как стартовый совместимый вариант;
- не требовать новых VLESS encryption/ML-DSA/fragmentation параметров до
  доказанной совместимости обеих мобильных версий;
- ограничение maxTimeDiff вводить после проверки синхронизации часов;
- запретить из прокси loopback, RFC1918, link-local/metadata, ULA и собственные
  адреса сервера, включая обход через DNS. Тестировать фактические IP назначения.

Скелет URI (не действующий доступ):

```text
vless://<DEVICE_UUID>@209.38.196.186:443?encryption=none&security=reality&type=tcp&flow=xtls-rprx-vision&sni=<VALIDATED_SNI>&fp=chrome&pbk=<CLIENT_KEY_MATERIAL>&sid=<SHORT_ID>#KudaVPN
```

Названия client password/publicKey и URI pbk проверить по закреплённой версии
Xray и фактическому импорту Happ. Приватный серверный ключ в ссылку не входит.
UUID и полная ссылка являются учётными данными: не писать их в логи/аналитику.
Не отправлять реальные subscription URLs во внешние сервисы шифрования ссылок.

## Интеграция с ботом и PostgreSQL — следующий этап

Текущий VPNService имеет заглушку WireGuard, Device требует public_key/assigned_ip
для active. Не подставлять туда вымышленные значения для VLESS.
Добавить безопасной миграцией protocol к VPNServer/Device и protocol-specific
credential таблицу/ограничения; сохранить старые WireGuard поля и данные.
Хранить UUID устройства, server/device IDs, opaque Xray label, shortId,
provisioning/revocation state; секрет UUID шифровать отдельным ключом вне БД.
PostgreSQL — источник желаемого состояния; отдельный provisioning worker
реализует XrayGateway, retries/outbox/idempotency и reconciliation.
Боту не выдавать root, SSH-ключ или Docker socket. Management API и worker
доступны только локально; ограничить доступ локальных пользователей к хосту.

При active и expires_at>now выдавать ссылку только после успешного AddUser.
Pending/expired/blocked запрещают выдачу; worker удаляет UUID через HandlerService.
API изменения не считать долговременной конфигурацией: после restart восстанавливать
только разрешённых пользователей, при ошибке синхронизации не возвращать старый
список просроченных клиентов. Проверить, что отключение запрещает новые подключения;
существующие потоки могут потребовать отдельного механизма завершения.
Статус device revoked выставлять после подтверждения удаления.
UUID не является аппаратной привязкой: ссылку можно скопировать на другой телефон.

Первый пилот: один явно разрешённый тестовый профиль, импорт буфером/QR.
Затем бот выдаёт персональную URI в текущем экране без новой polling-копии.
HTTPS subscription endpoint с длинным отзываемым токеном, хранением hash токена,
Cache-Control no-store и обновлением списка серверов — отдельный этап.
Для него нужен домен/TLS и решение конфликта 443; новых портов сейчас не открывать.
Трафик: StatsService по opaque label, сохранять дельты и учитывать сброс counters
при restart. Установить лимиты можно только после реализации enforcement.

## Секреты и резервирование

Конфигурация вне Git/build context, закрытый каталог 700; файл ключей 600 у
владельца UID Xray (либо 640 для строго выделенной группы). Боту приватный ключ
не нужен. Management API без публичного доступа; никаких веб-панелей.
Сохранять конфигурацию, версию/digest и PostgreSQL в зашифрованных off-host backups,
проверять восстановление изолированно; пароль шифрования не хранить рядом с dump.
Конфигурация содержит UUID и privateKey: не публиковать её при диагностике.
Access logging посещаемых сайтов отключить; оставить обезличенные ошибки,
статистику bytes, ресурсы, latency, рестарты и результаты контрольного подключения.

## Последовательность после отдельного подтверждения

1. Снять baseline состояния бота/БД/WireGuard, сохранить защищённые backups.
2. Проверить официальный release/digest, target и доступность 443 повторно.
3. Подготовить секреты и JSON; выполнить config test (`xray run -test`) без listener.
4. Запустить только Xray с пустым списком пользователей; проверить rootless,
   listeners/API/ограничения. Не выполнять compose down/up существующего проекта.
5. Добавить один тестовый UUID. Только по явному разрешению добавить одно правило
   UFW 443/tcp и при необходимости отдельное правило DO Cloud Firewall.
6. На iOS и Android: импорт, Wi-Fi/мобильная сеть, внешний IP, DNS/IPv6,
   браузер/видео/UDP, фон/переподключение, задержка/скорость.
   VPN не должен давать доступ к PostgreSQL, API, metadata и внутренним сетям.
7. Отозвать тестовый UUID, проверить запрет новых подключений; restart только Xray
   и проверить отсутствие возвращения отозванного доступа. Проверить WireGuard,
   бот health/readiness и PostgreSQL без их перезапуска.
8. После успешного пилота отдельно согласовать миграцию/worker/бот-интеграцию.

Откат: остановить только Xray, удалить только добавленное нами правило 443/tcp
(по точному соответствию, без reset UFW), закрыть добавленное cloud rule,
сохранить ключи/конфигурацию/диагностику. Бот, БД, WG и маршруты не трогать.
Для обновления Xray хранить прежний image/config; не выполнять schema downgrade.

## Фактическое исправление привязки TCP443

После установки v26.3.27 обнаружено: Docker с UID10001, cap_drop ALL,
cap_add NET_BIND_SERVICE и no-new-privileges оставляет capability в bounding set,
но effective/permitted/ambient пусты. На хосте unprivileged_port_start=1024.
Поэтому прежняя конфигурация не могла привязаться к 443.

Исправление без изменения UFW/NAT/маршрутов:

- Xray слушает только 127.0.0.1:10443 в host network; cap_add удалён.
- systemd unit kudavpn-xray-front.socket слушает 209.38.196.186:443.
  Порт открывает существующий systemd PID1, а не root-контейнер.
- kudavpn-xray-front.service использует штатный systemd-socket-proxyd для TCP
  relay на 127.0.0.1:10443; DynamicUser, NoNewPrivileges, пустые capabilities,
  ProtectSystem=strict и ограничения доступа сохранены.
- URI владельца по-прежнему содержит внешний TCP443; UUID/ключи не менялись.
- Xray healthy, UID10001, фактические capabilities=0, NoNewPrivs=1,
  read-only; restart count=0. TCP443 проверен с Mac.
- Bot/PostgreSQL StartedAt не изменились; их health/readiness и SSH/WG проверены.
- Проверка телефона ещё требуется: сетевой listener и config test не доказывают
  успешное аутентифицированное VPN-подключение.

Конфигурация и исходные backups остаются только на сервере /opt/kudavpn-xray.
Защищённая URI хранится на Mac /Users/gr/Documents/KudaVPN-private/owner-happ.txt.
Откат /opt/kudavpn-xray/rollback.sh останавливает только новый socket/service и
Xray-контейнер, отключает автозапуск нового socket; UFW и прежние сервисы не трогает.
Скрипт проверен sh -n; остановка рабочего Xray после успеха не выполнялась.
