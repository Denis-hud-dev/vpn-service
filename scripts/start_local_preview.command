#!/bin/sh
# Foreground loopback demo only. No launchd, VPS, .env or production credentials.
set -eu
preview_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$preview_root"
preview_python="$preview_root/.preview-venv/bin/python"
if [ ! -x "$preview_python" ]; then
    printf '%s\n' 'Не найдено локальное окружение .preview-venv.' 'Инструкция: docs/local-brand-preview-20261030.md'
    exit 1
fi
export PYTHONPATH="$preview_root"
if ! "$preview_python" -c 'import aiohttp, aiogram, sqlalchemy, aiosqlite, jwt, cryptography, PIL' >/dev/null 2>&1; then
    printf '%s\n' 'Не установлены зависимости предпросмотра.' 'Инструкция: docs/local-brand-preview-20261030.md'
    exit 1
fi
printf '%s\n' 'KudaVPN: только локальная имитация, без настоящего VPN и оплаты.' 'Сайт: http://127.0.0.1:18118/' 'Подключение: http://127.0.0.1:18118/__demo__/connect' 'Для остановки нажмите Ctrl+C. Оставьте это окно Terminal открытым.'
exec "$preview_python" -m scripts.preview_website --port 18118
