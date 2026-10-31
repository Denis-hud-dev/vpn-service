"""Hidden local entry of BotFather OIDC credentials. No network or deployment."""
import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import warnings

TARGET = Path.home()/'Documents/KudaVPN-private/website-https-review'
ORIGIN = 'https://r1276248.s2578.vm.mydomain.zone:20443'


def save(folder, client_id, client_secret):
    folder = Path(folder)
    if (not folder.is_absolute() or not re.fullmatch(r'[0-9]{4,20}', client_id)
            or not 12 <= len(client_secret) <= 512
            or any(not 33 <= ord(c) <= 126 for c in client_secret)):
        raise ValueError('Некорректный формат OIDC; ничего не сохранено.')
    for parent in (folder, *folder.parents):
        if parent.is_symlink() or (parent/'.git').exists():
            raise ValueError('Нужен приватный каталог вне Git без символических ссылок.')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.stat().st_mode & 0o777 != 0o700:
        raise ValueError('Приватный каталог должен иметь права 0700.')
    destination = folder/'oidc.json'
    if destination.exists() or destination.is_symlink():
        raise ValueError('OIDC уже сохранён; автоматическая перезапись запрещена.')
    fd, temporary = tempfile.mkstemp(prefix='.oidc-', dir=folder)
    try:
        with os.fdopen(fd,'w') as stream:
            os.fchmod(stream.fileno(),0o600)
            json.dump({'client_id':client_id,'client_secret':client_secret,'origin':ORIGIN,
                       'redirect_uri':ORIGIN+'/auth/callback'},stream)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic, no-overwrite publication; readers never see a partial JSON file.
        os.link(temporary,destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise SystemExit('Запустите помощник в интерактивном Terminal; скрытый ввод обязателен.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            client_id = getpass.getpass('OIDC Client ID из Login Widget: ')
            secret = getpass.getpass('OIDC Client Secret из Login Widget: ')
        save(TARGET,client_id,secret)
    except getpass.GetPassWarning:
        raise SystemExit('Скрытый ввод недоступен; ничего не сохранено.') from None
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except BaseException:
        raise SystemExit('Сохранение не завершено. Значения в чат не присылайте.') from None
    print('OIDC сохранён локально вне Git с правами 0600. Значения не показаны; сервер не менялся.')


if __name__ == '__main__':
    main()
