"""Atomically replace a locally saved test-shop secret via hidden Terminal input.

This never contacts YooKassa and never prints either the old or new secret.
"""
import argparse
import getpass
import hmac
import os
import re
import stat
import sys
import tempfile
from pathlib import Path


TARGET = Path.home() / 'Documents/KudaVPN-private/telegram-pilot'


def private_regular(path: Path) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    return stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o600


def main() -> None:
    parser = argparse.ArgumentParser(description='Replace only the local test-shop API secret')
    parser.add_argument('--shop-id', required=True)
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise SystemExit('Нужен интерактивный Terminal для скрытого ввода ключа.')
    if not re.fullmatch(r'[0-9]{4,20}', args.shop_id):
        raise SystemExit('Неверный ID тестового магазина.')
    if TARGET.is_symlink() or not TARGET.is_dir() or stat.S_IMODE(TARGET.stat().st_mode) != 0o700:
        raise SystemExit('Каталог тестовых секретов должен иметь права 0700.')
    shop_path = TARGET / 'yookassa-test-shop-id'
    secret_path = TARGET / 'yookassa-test-secret'
    if not private_regular(shop_path) or not private_regular(secret_path):
        raise SystemExit('Тестовые файлы должны быть обычными файлами с правами 0600.')
    if shop_path.read_text().strip() != args.shop_id:
        raise SystemExit('ID сохранённого тестового магазина не совпадает.')

    new_secret = getpass.getpass('Новый локальный Секретный API-ключ тестового магазина: ').strip()
    if not new_secret or any(ord(c) < 33 or ord(c) == 127 for c in new_secret):
        raise SystemExit('Неверный формат ключа; файл не менялся.')
    if hmac.compare_digest(new_secret, secret_path.read_text().strip()):
        raise SystemExit('Сохранён тот же ключ; файл не менялся.')

    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix='.yookassa-test-secret-', dir=TARGET)
        temporary = Path(name)
        with os.fdopen(fd, 'w') as stream:
            stream.write(new_secret + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        if stat.S_IMODE(temporary.stat().st_mode) != 0o600:
            raise RuntimeError('Временный файл имеет неверные права доступа.')
        os.replace(temporary, secret_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print('Локальный тестовый секрет обновлён; значение не показано.')


if __name__ == '__main__':
    main()
