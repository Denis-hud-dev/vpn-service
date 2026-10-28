# Website-only обновление PR29 — 28 октября2026 UTC

Адрес: https://r1276248.s2578.vm.mydomain.zone:20443/ — закрытый owner-only стенд, не продажи. Runtime **fe9cb519245d35cf297a195e5a0664132e5ef093**. [PR29](https://github.com/Denis-hud-dev/vpn-service/pull/29).

## Причина и минимальное исправление

Preflight `extras()` получал KeyError/MainPID у systemd timer: у таймера нет процесса и systemctl show не возвращает это поле. Сейчас отсутствие PID допускается только для активного `.timer`; `.service` должен быть active и иметь PID>1. Конфигурация/процессы защищённых служб по-прежнему сравниваются.15 профильных тестов, включая missing/zero PID сервиса и inactive timer, passed. До этого также исправлен способ запуска helper: существующий website venv вместо системного Python без зависимостей. Проверки не отключались, VPN не перенастраивался.

Оба CIfe9cb51 success: [PR](https://github.com/Denis-hud-dev/vpn-service/actions/runs/40637007600), [branch](https://github.com/Denis-hud-dev/vpn-service/actions/runs/40637002568), **492 passed,3 skipped**. Skip — прежний trusted TEST_XRAY_BINARY; рабочий Xray тестами не использовался.

## Что изменилось

Только `/etc/systemd/system/kudavpn-website-review.service.d/90-review-release.conf`: website WorkingDirectory закреплён за `/opt/kudavpn-website-review/site-release-fe9cb519245d`. Перезапущена только эта служба; user/credentials/TLS/env/20443/ограничения те же. Архив SHA256 проверен, runtime permissions755/644, dependency requirements идентичны прежним. Новых пакетов/серверов/платных услуг нет.

**Shared current остаётся `/opt/kudavpn-website-review/release-faca30b`.** Bridge и read-only exporter используют старую версию. Существующий test checkout/poller и бот не перезапускались. Durable reconciliation worker не включался: пользователь разрешил обновление только сайта. Внешний webhook/Stars/СБП sandbox/live ключи не включались. СБП поддерживается магазином по подтверждению владельца, но sandbox недоступна — это ожидаемо.

Миграции/переносы не выполнялись. Схемы `kudavpn_website_test=0009_website`, `kudavpn_telegram_pilot=0008_premium_notices` прежние. Подтверждение заказа/защищённый Happ CTA нового сайта используют старый совместимый Unix payment bridge и прежний API status polling; другого источника подписок/UUID нет.

## Backup и откат

Закрытый root-only каталог: `/var/backups/kudavpn-website-review/site-fe9cb519245d`.

Сохранены конфигурация сайта, исходный unit/drop-in state, validated custom pg_dump обеих тестовых БД, hashes канонических данных/идентичности профиля/ссылки Happ, protected service manifest. Pg_dump проверены pg_restore --list и decode в /dev/null; это проверка backup-формата, не заявленная репетиция восстановления рабочей БД. Restore/drop-in rehearsal прошла на приватном временном файле, rollback script прошёл sh -n и profile tests.

Точная аварийная команда на JustHost от root: `sh /var/backups/kudavpn-website-review/site-fe9cb519245d/rollback.sh`. Возвращается только прежний website WorkingDirectory/config, затем restart только website и readiness/protected checks. Не останавливать bridge, не восстанавливать старый pg_dump поверх новых оплат, не применять исторический rollback20261027-payments на read-only28f9c23. Реальная авария не возникла, фактический откат при успешном rollout не выполнялся. Повторно activate поверх existing activated marker не запускать; использовать verify.

## Фактически проверено

- Website ready=true, TLS доверенный; anonymous `/`, `/account`, `/connect` возвращают401.
- Прежний personal feed HTTP200, ровно1 VLESS узел10443; URL hash прежний. Bearer/UUID/данные покупателя не печатались. Feed plaintext, не Base64; первоначальная ошибка check-parser исправлена только в проверке.
- Полный canonical digest payments/subscriptions/history/tokens и profile identity совпал до/после rollout. Node lease revisions сознательно не сравниваются как immutable — они могут штатно обновляться в фоне.
- Protected PID/unit/config/listener baseline и extra bridge/Monitor/timer baseline совпали. TCP443/8443/9443/10443 доступны. Основной бот/DO/production/Monitor/VPN не изменялись.
- Старая browser session истекла; нормальный OAuth начат, official Telegram form ожидает подтверждение владельца. Криптография/state/PKCE/allowlist не ослаблялись. Полноценный новый login/account/payment UI на05:22UTC ещё pending, не выдавать redirect за успешный вход.

## Продолжение после ответа владельца

Вопрос о подтверждении свежего Telegram login уже задан; текущая вкладка10 markHandoff. После «Готово» немедленно проверить её результат. Не переиспользовать code/state из истории, не запускать новый flow при действующем. При expiration понадобится ровно один новый normal login; повторять rollout не нужно.

После входа проверить цены199/499/1790, новую confirmation panel, отсутствие нового заказа до её подтверждения, защищённый Happ/session и повторный refresh. Оплату сам владелец выполняет тестовой картой ЮKassa после явной инструкции; реальных карт/денег не использовать. На этом этапе новых платежей не создано и подписка не продлевалась. После его оплаты — API verification/одна history grant/сохранение UUID и URL. До реальных продаж ещё не закрыты live-карта/СБП/возврат, НПД регистрация+доставка чека и юридические документы.
