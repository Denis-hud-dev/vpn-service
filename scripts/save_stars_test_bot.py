"""Hidden local input for a separate Telegram TEST-server bot. Never starts it."""
import getpass
import json
import os
from pathlib import Path
import re
import ssl
import sys
import urllib.request
import warnings


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def verify_test_bot(token,*,opener=None):
    if not isinstance(token,str) or not re.fullmatch(r'[1-9][0-9]{0,18}:[A-Za-z0-9_-]{10,256}',token):
        raise ValueError('Invalid credential')
    # Never try the ordinary endpoint or fall back to the production namespace.
    opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),NoRedirect())
    request=urllib.request.Request('https://api.telegram.org/bot'+token+'/test/getMe')
    try:
        with opener.open(request,timeout=10) as response:
            if response.status!=200:raise ValueError()
            raw=response.read(65537)
        if len(raw)>65536:raise ValueError()
        data=json.loads(raw)
        bot=data['result']
        if (data.get('ok') is not True or bot.get('is_bot') is not True or type(bot.get('id')) is not int
                or bot['id']!=int(token.split(':',1)[0])
                or not re.fullmatch(r'[A-Za-z0-9_]{5,32}',bot.get('username',''))):raise ValueError()
        return {'bot_id':bot['id'],'bot_username':bot['username']}
    except Exception:
        # urllib exceptions contain token-bearing URLs. Never print/re-raise them.
        raise ValueError('Telegram TEST bot verification failed') from None


def save_private(path,token,identity):
    if set(identity)!={'bot_id','bot_username'}:raise ValueError('Verified bot identity required')
    path=Path(path)
    if not path.is_absolute() or path.is_symlink() or path.exists():raise ValueError('New private file required')
    parent=path.parent
    if parent.is_symlink() or not parent.is_dir():raise ValueError('Private directory required')
    if parent.stat().st_uid!=os.geteuid() or parent.stat().st_mode & 0o077:raise ValueError('Private directory required')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:
        os.fchmod(stream.fileno(),0o600)
        json.dump({'environment':'telegram_test','enabled':False,'token':token,**identity,
                   'approved_test_prices':{'1':1,'3':2,'12':3}},stream)
        stream.flush();os.fsync(stream.fileno())


def main():
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise SystemExit('Run in Mac Terminal; hidden input requires a terminal')
    parent=Path.home()/'Documents/KudaVPN-private/telegram-stars-test'
    root=parent.parent
    try:
        if root.is_symlink() or not root.is_dir() or root.stat().st_uid!=os.geteuid() or root.stat().st_mode & 0o077:
            raise ValueError()
        parent.mkdir(mode=0o700,exist_ok=True)
        if parent.is_symlink() or parent.stat().st_uid!=os.geteuid() or parent.stat().st_mode & 0o077:raise ValueError()
        target=parent/'bot.json'
        if target.exists() or target.is_symlink():raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter('error',getpass.GetPassWarning)
            token=getpass.getpass('Token of the separate Telegram TEST-server bot (hidden): ')
        identity=verify_test_bot(token)
        save_private(target,token,identity)
        token=None
        print('Telegram TEST bot verified. Saved privately with mode0600. Payments remain disabled.')
    except (Exception,KeyboardInterrupt):
        raise SystemExit('Not saved. Check the TEST-server bot and private directory; secret details withheld.') from None


if __name__=='__main__':main()
