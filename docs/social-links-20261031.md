# Прямые ссылки TEST-бота — 31 октября 2026

В 08:20:16 UTC (01:20:16 PDT) обновлена только конфигурация @KudaVPN_Test_Bot на JustHost. Перезапущена только `kudavpn-telegram-pilot-bot.service`; PID после обновления — 843525.

## Изменение

Добавлен серверный файл `/etc/systemd/system/kudavpn-telegram-pilot-bot.service.d/99-social-links.conf`:

```ini
[Service]
Environment=PILOT_MENU_CHANNEL_URL=https://t.me/KudaVPN_official
Environment=PILOT_MENU_COMMUNITY_URL=https://t.me/KudaVPN_community
```

Существующий код меню автоматически использует URL-кнопки вместо заглушек. Сайт остался прежним: `https://r1276248.s2578.vm.mydomain.zone:20443`. Тексты, порядок, фирменные иконки и нижняя клавиатура сохранены. Runtime `/opt/kudavpn-telegram-pilot/app-menu-links-ab72c76b9e4c`, исходники, прежние настройки и параметры запуска не менялись.

## Проверки

- **28 тестов прошли, 0 пропущено, 1,84 с**: `tests/test_social_menu_urls.py` и `tests/test_public_menu_links.py`. Проверены адреса, URL-кнопки, иконки, первый и повторный `/start`, возврат, сохранение баннера и важных уведомлений, изоляция окружения и откат.
- Реальный `getMe` подтвердил TEST-бота. Read-only `getChat` подтвердил канал `KudaVPN_official` («KudaVPN | Новости») и supergroup `KudaVPN_community` («KudaVPN | Сообщество»). Сообщения в эти чаты не отправлялись.
- В Telegram на Mac выполнены `/start` и повторный `/start`: три URL-кнопки видны со стрелками, повторного баннера нет. Нажатия самих inline-ссылок инструментом не проверялись; точные адреса подтверждены фактическим markup действующего runtime и API Telegram.
- Readiness бота, сайта и HTTPS-подписки успешен. После повторного `/start` проведена дополнительная проверка: новых ERROR/Traceback/Telegram API и сетевых ошибок — **0**.
- Хеши исходников и защищённый снимок остальных служб, конфигураций и слушающих портов совпали. PostgreSQL, Xray, HTTPS-сервисы, Monitor и автоматическое резервирование не менялись и не перезапускались. SQL/миграции не выполнялись. Штатные `/start` обрабатывались прежним кодом навигации.
- DigitalOcean, основной бот, UUID, подписки, Happ, платежи и авторизация не изменялись. Полный аудит и полный локальный набор тестов не повторялись.

## Резервная копия и откат

Каталог: `/var/backups/kudavpn-telegram-pilot/social-links-20261031` (0700).

Архив `configuration-and-runtime.tar.age`: **5 561 864 байта**, 0600, SHA256 проверен. Содержит прежний runtime, конфигурацию, systemd-параметры, эффективное окружение и согласованный технический onboarding-ledger. Секретное окружение зашифровано непосредственно из памяти. Приватный ключ восстановления не запрашивался и на сервер не передавался.

```sh
sh /var/backups/kudavpn-telegram-pilot/social-links-20261031/rollback.sh
```

Откат удаляет только собственный `99-social-links.conf` при точном совпадении содержимого, перечитывает systemd и перезапускает только TEST-бота. Базы и пользовательские состояния не восстанавливать. Отказ запуска/MainPID 0 обработан тестом. Живой откат не понадобился.

Сохранить установщик `/var/lib/kudavpn-ui-rollouts/social-links-20261031/configure_social_links.py` и прежние проверенные helper/guard в `/var/lib/kudavpn-ui-rollouts/menu-ab72c76b9e4c`. Их хеши закреплены в установщике. Старые копии не удалялись.

## Репозиторий

Изменены только конфигурационный drop-in, установщик, целевые тесты, этот отчёт, JSON-результат и handoff. Код `bot/` не менялся.

Рабочая папка PR #29: `/Users/gr/Documents/KudaVPN-social-links`, ветка `codex/standalone-site-v1-20261026`. Неутверждённые шесть эмодзи сохранены отдельно на локальной ветке `codex/community-emoji-review-20261030`, коммит `ab4fb33`, в `/Users/gr/Documents/KudaVPN-preview`; в это обновление они не входят. Удалена только устаревшая Git-регистрация уже отсутствовавшего временного worktree, файлы и коммиты не удалялись.

Скриншот настоящего меню хранится вне Git: `/Users/gr/Documents/KudaVPN-private/social-links-20261031/menu.jpg`. Канал и группа уже существовали; ничего не создавалось и их настройки не менялись.
