"""Save *test-shop* YooKassa credentials outside Git, with hidden input.

Run manually on the owner's Mac. This script never contacts YooKassa or the VPS.
"""
import getpass
import argparse
import os
import re
import stat
import sys
from pathlib import Path


TARGET = Path.home() / 'Documents/KudaVPN-private/telegram-pilot'


def save_new(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(value + '\n')
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description='Save isolated YooKassa test-shop credentials')
    parser.add_argument('--shop-id', help='Non-secret ID of the test shop')
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise SystemExit('Нужен интерактивный Terminal для скрытого ввода ключа.')
    TARGET.mkdir(mode=0o700, parents=True, exist_ok=True)
    if TARGET.is_symlink() or not TARGET.is_dir() or stat.S_IMODE(TARGET.stat().st_mode) != 0o700:
        raise SystemExit('Каталог тестовых секретов должен быть обычным каталогом с правами 0700.')
    shop_path = TARGET / 'yookassa-test-shop-id'
    secret_path = TARGET / 'yookassa-test-secret'
    if shop_path.exists() or shop_path.is_symlink() or secret_path.exists() or secret_path.is_symlink():
        raise SystemExit('Файлы уже существуют; автоматическая перезапись запрещена.')
    shop_id = args.shop_id.strip() if args.shop_id else getpass.getpass('ID только ТЕСТОВОГО магазина YooKassa: ').strip()
    secret = getpass.getpass('Секретный ключ только ТЕСТОВОГО магазина: ').strip()
    if not re.fullmatch(r'[0-9]{4,20}', shop_id) or not secret or '\n' in secret:
        raise SystemExit('Неверный формат; файлы не созданы.')
    try:
        save_new(shop_path, shop_id)
        save_new(secret_path, secret)
    except BaseException:
        shop_path.unlink(missing_ok=True)
        secret_path.unlink(missing_ok=True)
        raise
    print('Тестовые учётные данные сохранены вне Git с правами 0600; значения не показаны.')


if __name__ == '__main__':
    main()
