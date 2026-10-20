"""Save *test-shop* YooKassa credentials outside Git, with hidden input.

Run manually on the owner's Mac. This script never contacts YooKassa or the VPS.
"""
import getpass
import os
import re
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
    TARGET.mkdir(mode=0o700, parents=True, exist_ok=True)
    shop_path = TARGET / 'yookassa-test-shop-id'
    secret_path = TARGET / 'yookassa-test-secret'
    if shop_path.exists() or secret_path.exists():
        raise SystemExit('Файлы уже существуют; автоматическая перезапись запрещена.')
    shop_id = getpass.getpass('ID только ТЕСТОВОГО магазина YooKassa: ').strip()
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
