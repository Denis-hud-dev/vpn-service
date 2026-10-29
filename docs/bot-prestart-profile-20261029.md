# Профиль до Start и навигация без служебной подсказки — 29 октября2026

## Официальные возможности

setMyDescription задаёт описание пустого чата (до512символов); setMyShortDescription — About профиля/превью ссылки (до120). Подготовлены default и ru,211/89символов. Это настройки Telegram, не сообщения до start. Системную кнопку Start и произвольные inline-кнопки на пустом экране бот не размещает. Фото/видео описания официально поддерживается через BotFather Edit Description Picture; Bot API поля для него нет. Публичные сообщения перед start не отправляются. setMyProfilePhoto принимает новый JPEG: используется готовый оригинальный квадратный JPEG1254×1254, без генерации/редактирования/перемещения K. SVG/свет/орбита/баннер не менялись.

Источники: https://core.telegram.org/bots/api#setmydescription ; https://core.telegram.org/bots/api#setmyshortdescription ; https://core.telegram.org/bots/api#setmyprofilephoto ; https://core.telegram.org/bots/features#about-text-description-and-profile-media .

## Интерфейс

ReplyKeyboard и inline keyboard нельзя прикрепить к одному сообщению. Первый start: оригинальный баннер с компактной caption(преимущества+1/2/3), ReplyKeyboard на этом же баннере; сразу следом короткий tracked inline-экран действий с покупкой первой строкой. Баннер не отслеживается как удаляемая навигация. Повторный start: содержательное компактное приветствие с реальным статусом/сроком и ReplyKeyboard, затем inline-действия. Это взаимодополняющие блоки, не два одинаковых приветствия. Предыдущие carrier/nav удаляются только по существующим безопасным указателям. Служебная фраза больше не отправляется. Важные уведомления/баннер сохраняются.

Нижняя клавиатура: первая отдельная строка Купить VPN(premium), вторая Подключиться(connect), третья Личный кабинет(subscription) | Информация(info). Прежние Unicode и чистые подписи поддерживаются; отказ API custom emoji → Unicode fallback. Покупка открывает только существующий каталог199/499/1790, без order/invoice/external checkout/Stars. «📖 Инструкция» получает существующий connect ID5389102861672032499. Новых иконок/ID нет.

## Безопасность и проверки

Локально128 различных профильных проверок (45+65+one existing-avatar rollback+17 journeys) прошли. Профильный updater проверяет getMe=KudaVPN_Test_Bot до записи, root/immutable source/isolated DB env; сохраняет описание default/ru/About/старую аватарку root0600 в0700. Только публичные параметры тестового бота; нет отправки сообщений, контактов, платежей или изменения других ботов. apply делает readback, при ошибке restore; проверены wrong production identity, timeout после фактической смены картинки и восстановление предыдущей/отсутствующей аватарки. Токен только из защищённого env процесса, никогда не выводится. UI-deploy отдельно защищает прежний runtime66a74f2,6 UI-файлов, backup/rollback и неизменность всех остальных bytes/PID/configs/listeners/financial/profile digest. Полный PR29/0010 не запускать как bot.

Изображения/тексты без изменения пикселей сохранены в /Users/gr/Downloads/KudaVPN-Test-Bot-Profile-20261029: avatar.jpg,description-banner.png,description.txt,about.txt. Баннер доStart пока устанавливается вручную: BotFather → /mybots → @KudaVPN_Test_Bot → Edit Bot → Edit Description Picture. Inline-кнопки этого административного интерфейса CUA не показывает надёжно; выбирать вслепую среди production/test не допускается. Если BotFather попросит специальный размер картинки, следовать показанным им требованиям; текущий исходный баннер1200×628 не преобразовывался. Не считать его установку выполненной.

Физический Android доStart не доступен в этой сессии. Нельзя очищать переписку владельца ради пустого чата. Проверка Android — аккаунтом, который ещё не запускал бота; backend allowlist тестового бота не расширяется. Итог API/CI/развёртывания будет дописан после фактического выполнения.
