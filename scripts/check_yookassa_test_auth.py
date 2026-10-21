"""Read-only authentication check for a YooKassa test shop.

Sends exactly one GET to the official API. Never creates a payment, follows a
redirect, or prints response bodies or credentials.
"""
import argparse
import base64
import http.client
import re
import ssl
import stat
from pathlib import Path


TARGET = Path.home() / 'Documents/KudaVPN-private/telegram-pilot'
HOST = 'api.yookassa.ru'


def read_private_file(path: Path) -> str:
    mode = path.lstat().st_mode
    if not stat.S_ISREG(mode) or stat.S_IMODE(mode) != 0o600:
        raise ValueError('Файлы тестового магазина должны быть обычными файлами с правами 0600.')
    value = path.read_text().strip()
    if not value or '\n' in value:
        raise ValueError('Неверный формат файла тестового магазина.')
    return value


def check(shop_id: str) -> bool:
    if not re.fullmatch(r'[0-9]{4,20}', shop_id):
        raise ValueError('Неверный ID тестового магазина.')
    if TARGET.is_symlink() or not TARGET.is_dir() or stat.S_IMODE(TARGET.stat().st_mode) != 0o700:
        raise ValueError('Каталог тестовых секретов должен иметь права 0700.')
    saved_id = read_private_file(TARGET / 'yookassa-test-shop-id')
    secret = read_private_file(TARGET / 'yookassa-test-secret')
    if saved_id != shop_id:
        raise ValueError('ID сохранённого тестового магазина не совпадает.')

    credential = base64.b64encode(f'{saved_id}:{secret}'.encode()).decode()
    connection = http.client.HTTPSConnection(HOST, timeout=12, context=ssl.create_default_context())
    try:
        connection.request('GET', '/v3/payments?limit=1', headers={
            'Authorization': 'Basic ' + credential,
            'Accept': 'application/json',
        })
        response = connection.getresponse()
        return response.status == 200
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Read-only YooKassa test-shop authentication check')
    parser.add_argument('--shop-id', required=True)
    args = parser.parse_args()
    try:
        success = check(args.shop_id)
    except (OSError, ValueError, ssl.SSLError, http.client.HTTPException):
        success = False
    print('Успешно' if success else 'Ошибка')
    if not success:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
