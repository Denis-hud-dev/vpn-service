# Закрытый сайт PR №29: единая подписка и Telegram OIDC

## Текущее состояние

Адрес `https://r1276248.s2578.vm.mydomain.zone:20443` и callback `/auth/callback` одобрены владельцем. **Сайт не развёрнут, listener не открыт.** Последнее указание владельца: сначала подтверждённые выводы/план авторизации, без серверных изменений. Production, Xray, боты, Monitor и DigitalOcean не менялись.

Владелец сообщил об успешном `/setdomain` у @KudaVPN_Test_Bot. Это legacy domain pairing, а не регистрация OIDC. Client ID/Secret и регистрация двух полных Allowed URLs пока не подтверждены. Включать собственную Mini App бота не требуется.

## Проверенные официальные сведения об авторизации

Источники проверены 26 октября 2026:

- [Текущий Telegram Login/OIDC](https://core.telegram.org/bots/telegram-login#registering-your-allowed-urls): открыть **мини-приложение самого @BotFather**, выбрать бота, затем Login Widget. Зарегистрировать origin и точный redirect URI. В этом разделе Telegram показывает Client ID и Client Secret. Это другой интерфейс, чем текстовое `/mybots → Bot Settings → Configure Mini App`; Enable Mini App у @KudaVPN_Test_Bot не нажимать.
- Официальная ссылка из документации открывает менеджер: [BotFather mini app](https://t.me/botfather?startapp=). В этой сессии переход t.me → native Telegram был заблокирован инструментом; ограничение не обходили. Конкретный экран аккаунта владельца агент не видел; отсутствие пункта в новом интерфейсе нельзя объяснять догадкой о Premium, типе аккаунта или необходимости включить собственную Mini App.
- Публичный [OIDC discovery](https://oauth.telegram.org/.well-known/openid-configuration) получен GET без credentials. Подтверждены issuer/oauth endpoints, response_type=code, S256 и RS256. Вход в коде запрашивает только openid/profile, не телефон и bot_access. Режим RS256 совместим с текущей проверкой подписи.
- В официальной инструкции **нет явного правила о нестандартных HTTPS-портах**. URL20443 синтаксически допустим, сертификат проверяет имя, но принятие его Telegram не доказано. Без зарегистрированных Allowed URLs и клиента полноценная проверка редиректа невозможна. Не объявлять порт поддержанным или запрещённым, не подменять callback и не занимать рабочий443.
- Если Login Widget отсутствует и в новом менеджере: официальный документ направляет в @BotSupport с #oidc. Самостоятельно обращение не отправлялось. Не выдумывать команду выдачи Client Secret и не подставлять Bot API token вместо него.
- Альтернатива после согласования: [документированный legacy Login Widget](https://core.telegram.org/widgets/login-legacy), уже выполненный `/setdomain`, серверная HMAC-SHA256-проверка, ограничение auth_date, одноразовая привязка к браузеру, owner allowlist и отзываемый сеанс. Можно использовать отдельный производный verification key SHA256(test_bot_token), не передавая сайту сам Bot API token. Это другой механизм, **не OIDC**; в этой ветке не включён. Поддержку нестандартного порта для него также сначала проверить. Если потребуется стандартный443, согласовать отдельный безопасный HTTPS origin, не заменяя Xray/общий reverse proxy автоматически.

Точные Allowed URLs:

```text
https://r1276248.s2578.vm.mydomain.zone:20443
https://r1276248.s2578.vm.mydomain.zone:20443/auth/callback
```

При получении OIDC credentials владелец запускает в Mac Terminal:

```sh
python3 /Users/gr/Documents/KudaVPN-private/website-https-review/save-website-oidc.py
```

Оба поля вводятся скрыто, без значений в аргументах/чате/скриншотах. Помощник проверен python3 py_compile и тестами; refuses non-TTY/небезопасный fallback getpass, symlink/Git и перезапись. Папка0700, атомарный oidc.json0600 вне Git. Ничего не отправляет в сеть. Пока OIDC-раздел не найден, заполнять его нечем: ввод не повторять.

## Реализованная граница данных

- `ReviewAuth` хранит в отдельной auth-БД только login flows и review sessions. Подписанный Telegram ID проверяется по единственному приватно настроенному owner ID. Не создаёт копий канонических User/Subscription/Payment/UUID. ID из URL, тела запроса или неподписанной личности не принимается.
- Закрытый middleware защищает главную, кабинет, тарифы, историю, документы, assets и `/connect`. Анонимно доступны только минимальная форма входа и её CSS/logo, auth endpoints. `/ready` без входа — только loopback. Чужой подтверждённый аккаунт отвергается до записи сеанса. Cookie HttpOnly/Secure/__Host/SameSite, одноразовый state+PKCE и Origin/CSRF сохраняются. Access logs выключены.
- `website_export` — отдельный фиксированный доверенный читатель существующей изолированной БД kudavpn_telegram_pilot. Каждая PostgreSQL-транзакция REPEATABLE READ, READ ONLY; включены default_transaction_read_only, тайм-ауты, hide_parameters. Только фиксированные SELECT, без SQL/команд/идентификаторов из HTTP. Никаких новых grants/ролей/миграций в исходной БД не выполнялось.
- Читатель повторно использует entitled, node health/lease/digest и HMAC-деривацию **существующего** SubscriptionToken. Не вызывает issue, не создаёт/вращает nonce, UUID и токены, не запускает reconciler. Возвращает прежнюю личную ссылку только при подтверждённом праве и готовности10443.
- Будущий exporter root-сервис читает env только изолированного test bot и пишет атомарный snapshot root:website-group0640 в root-owned0750. Website UID не должен владеть каталогом, иметь исходный DATABASE_URL, Bot API token, HMAC key, агентские/SSH/платёжные credentials. Root-reader использует существующий DB credential внутри READ ONLY; это не отдельная DB-role с урезанными grants. Приложению эта учётная запись не выдаётся.
- Cache включает только владельца, display name, сохранённый тариф, статус/окончание, публичные имена готовых узлов, существующий bearer URL и до30 собственных test payment строк. UUID/ключи/данные карт не экспортируются. Веб-читатель требует root ownership/ограниченные права, ожидаемого владельца, точный origin/path feed, возраст<120 секунд. Нет/устарел/повреждён cache →503, без утечки содержимого или ложного успеха. Истёкшая подписка лишается кнопки доступа.
- Кабинет показывает реальную историю канонического тестового контура. Сохранённые цены прежних покупок не подменяются текущими. Чек для этих тестовых операций не формируется; это не live-НПД-реестр.
- Каталог199/499/1790 сохранён. В закрытом review выбор — `selection_only`, без заказа/запроса ЮKassa/платежа/продления. Платёжный прототип и отдельное локальное демо покупки сохранены, но серверный review entrypoint не получает provider и не запускает payment poller. Локальная имитация покупки не влияет на настоящую подписку.

## Проверки

- 53 passed,1 skipped: website/auth/review/secret saver/database. Локальный пропуск disposable PostgreSQL предусмотрен CI. Syntax/py_compile и diff/hygiene checks пройдены. Тест экспортера запрещает любые SQL кроме SELECT и доказывает сохранение прежнего bearer; auth-only login не создаёт canonical rows. Проверены чужой владелец, CSRF, logout, stale/missing/изменённый cache, неверный feed origin, каталог, неизменность снимка при повторном выборе.
- На JustHost однократно выполнен fixed reader **без установки файлов/служб**, с `SHOW transaction_read_only=on`. Подтверждены существующий owner, активная подписка,3 строки history и готовность прежней feed-ссылки. Значения имени/ID, ссылки, UUID и credentials не выводились. Срок/платежи не изменялись.
- Отдельный локальный preview `python -m scripts.preview_website_review --port 18107`, только loopback и синтетический snapshot/SQLite. Проверены390px и обычный desktop, нет horizontal overflow; выбор без оплаты, выход → закрытая форма, переход Happ, copy, iOS-инструкция и SVG QR. Физический Android/iPhone и настоящий Telegram callback пока не проверены. Скриншоты website-review-*.png содержат только вымышленные данные.

## Следующее развёртывание — пока не выполнять

1. Найти OIDC в настоящем BotFather manager или согласовать legacy. Сначала доказать принятие origin/callback, получить credentials через private helper. Не включать собственную Mini App, не менять основной бот.
2. После нового разрешения создать только отдельный website auth DB/user и service UID без shell; применить0009 **только** в новой БД. Все канонические таблицы там остаются пустыми. Никаких миграций существующего pilot/production.
3. Сохранить private manifest/hash/PID защищённых служб и архив новых тестовых настроек. Проверить восстановимость дампа только auth-БД. Не клонировать каноническую БД для второго lifecycle/controller.
4. Разместить root-owned immutable release/venv, закрытые OIDC/owner/TLS credentials через systemd LoadCredential. Root exporter oneshot+timer каждые30 секунд; ограничить write access только export dir. Website непривилегирован, ReadOnlyPaths code/cache, ProtectSystem/NoNewPrivileges/PrivateTmp, Resource limits, запрет доступа к исходным конфигам. Сайт должен видеть только auth DB/snapshot. Создать изолированные unit names, не редактировать существующие units.
5. Доказать локально401 для anonymous/foreign, read-only scope, отсутствие secret paths/access logs. Только затем bind20443 (`SITE_APPROVED_PUBLIC_REVIEW=1`, точный approved origin) при действующем trusted TLS. Не менять firewall/reverse proxy без отдельного решения. Backend без OIDC/owner/cache не должен проходить readiness.
6. Настоящий вход владельца, отказ второму аккаунту, real account/history/source expiry, existing Happ feed, logout/replay/CSRF. Реальный импорт/подключение на Android подтверждает владелец, без публикации URL/QR в отчётах. Платежи не запускать.
7. При ошибке stop/disable **только новые** website-review/export units и listener20443, сохранить диагностику, вернуть только собственные файлы/authDB. Не восстанавливать каноническую БД поверх данных. Проверить hash/PID/доступность прежних служб. Мониторинг/DO не трогать.

До выполнения этих пунктов20443 — согласованный будущий адрес, не действующий сайт. Минимальная рекомендация сейчас: открыть мини-приложение самого BotFather, а не включать Mini App у KudaVPN. При отсутствии OIDC-пункта не повторять поиск бесконечно: запросить #oidc поддержку или отдельно согласовать legacy.
