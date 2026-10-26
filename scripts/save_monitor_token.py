"""Hidden owner input; store only outside Git, never echo token or API URLs."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.request
import warnings


def telegram(token, method, payload):
    request = urllib.request.Request('https://api.telegram.org/bot'+token+'/'+method,
        data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=15) as response:
            data=json.load(response)
        if data.get('ok') is not True: raise ValueError()
        return data['result']
    except Exception:
        raise ValueError('Telegram не подтвердил служебного бота. Значения не выводятся.') from None


def private_write(path, data):
    if path.is_symlink():
        raise ValueError('Символические ссылки для секретов запрещены.')
    fd,temporary=tempfile.mkstemp(prefix='.monitor-save-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as handle:
            os.fchmod(handle.fileno(),0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--private-dir',type=Path,default=Path.home()/'Documents/KudaVPN-private/monitor')
    folder=parser.parse_args().private_dir.resolve()
    if any((p/'.git').exists() for p in (folder,*folder.parents)) or folder.stat().st_mode & 0o077:
        raise SystemExit('Требуется заранее подготовленная приватная папка0700 вне Git.')
    owner_file=folder/'owner-config.json'
    if owner_file.is_symlink() or owner_file.stat().st_mode & 0o077:
        raise SystemExit('Файл владельца должен быть0600 и не быть символической ссылкой.')
    config=json.loads(owner_file.read_text())
    owner=config['owner_id']
    if type(owner) is not int or owner<=0: raise SystemExit('Владелец не подтверждён.')
    if not sys.stdin.isatty(): raise SystemExit('Запустите скрипт непосредственно в Terminal: ввод должен быть скрытым.')
    try:
        with warnings.catch_warnings():
            # getpass must never fall back to visible stdin, even if /dev/tty
            # becomes unavailable after the isatty check.
            warnings.simplefilter('error',getpass.GetPassWarning)
            token=getpass.getpass('Токен только KudaVPN Monitor (ввод скрыт): ').strip()
    except (getpass.GetPassWarning,EOFError,KeyboardInterrupt):
        raise SystemExit('Скрытый ввод недоступен или отменён. Токен не сохранён.') from None
    if not re.fullmatch(r'[1-9][0-9]*:[A-Za-z0-9_-]{20,}',token): raise SystemExit('Некорректный формат токена.')
    try:
        me=telegram(token,'getMe',{})
        name=(me.get('username','')+' '+me.get('first_name','')).casefold()
        if (not me.get('is_bot') or 'kuda' not in name or 'monitor' not in name
                or token.partition(':')[0]!=str(me.get('id'))
                or me['id'] in config.get('application_bot_ids',[])):
            raise ValueError('Это не отдельный KudaVPN Monitor. Основной и тестовый боты запрещены.')
        updates=telegram(token,'getUpdates',{'timeout':0,'allowed_updates':['message']})
        # Separate the generator from its predicate: the original version had
        # an unclosed parenthesis here, reported on the following update line.
        started=any(
            message.get('chat',{}).get('type')=='private'
            and message['chat'].get('id')==owner
            and message.get('from',{}).get('id')==owner
            and message.get('text','').split(' ',1)[0].split('@',1)[0]=='/start'
            for message in (u.get('message',{}) for u in updates)
        )
        config.update(bot_username=me['username'],bot_id=me['id'],owner_start_verified=started,
                      initial_update_offset=max((u['update_id'] for u in updates),default=-1)+1)
        private_write(folder/'bot-token',token+'\n')
        private_write(owner_file,json.dumps(config)+'\n')
        print('Токен KudaVPN Monitor сохранён локально с правами0600; в Git не добавлен.')
        print('Start владельца подтверждён.' if started else 'Start не найден среди текущих обновлений. Нажмите /start в Monitor ещё раз.')
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except Exception:
        raise SystemExit('Сохранение не завершено. Сообщите только этот текст; токен не присылайте.') from None


if __name__=='__main__': main()
