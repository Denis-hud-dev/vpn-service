"""Overlay only reviewed UI files in a clone of the existing TEST bot runtime.

The full website branch has a different DB head; never boot it as the pilot bot.
No migrations, payment configuration, VPN files or other service restarts.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import urlsplit

BOT='kudavpn-telegram-pilot-bot.service'
ROOT=Path('/opt/kudavpn-telegram-pilot')
OLD=ROOT/'app-purchase-menu-e3bbf2136861'
DROPIN=Path('/etc/systemd/system')/(BOT+'.d')/'98-purchase-menu.conf'
FILES=('bot/keyboards/main.py','bot/screens.py','bot/premium_emoji.py',
       'bot/handlers/start.py','bot/navigation.py','bot/keyboards/reply.py')


def restore_dropin(backup):
    shutil.copy2(backup/'previous-dropin',DROPIN)


def bot_ready(run, port):
    # /health is only liveness: {"status":"alive"}. /ready checks startup
    # and the database, returning HTTP503 until the bot can serve updates.
    return json.loads(run(['curl','--silent','--show-error','--fail','--max-time','5',
                           'http://127.0.0.1:'+str(port)+'/ready']))=={'ready':True}


def signatures(path):
    return {node.name:ast.dump(node.args) for node in ast.parse(path.read_text()).body
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))}


def validate_overlay(old,new):
    for name in FILES:
        if not (old/name).is_file() or not (new/name).is_file() or (new/name).is_symlink():
            raise ValueError('Exact UI source files required')
        previous, updated = signatures(old/name), signatures(new/name)
        # Reviewed backwards-compatible optional switch, not an arbitrary API change.
        if name == "bot/handlers/start.py":
            node=next((n for n in ast.parse((new/name).read_text()).body if isinstance(n,ast.AsyncFunctionDef) and n.name=="install_reply_keyboard"),None)
            args=node.args if node else ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[])
            if node and [a.arg for a in args.kwonlyargs]==["custom_emoji"] and len(args.kw_defaults)==1 and isinstance(args.kw_defaults[0],ast.Constant) and args.kw_defaults[0].value is False:
                args.kwonlyargs=[];args.kw_defaults=[]
                updated[node.name]=ast.dump(args)
        additions = set(updated) - set(previous)
        allowed = {"bot/handlers/start.py": {"information"}, "bot/screens.py": {"first_welcome_text"},
                   "bot/premium_emoji.py": {"decorate_reply"}}.get(name,set())
        if (any(updated.get(key) != value for key, value in previous.items())
                or additions - allowed):
            raise ValueError('UI signatures differ from working runtime')


def code_digest(folder):
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts
            and str(p.relative_to(folder)) not in FILES}


def main(revision):
    if os.geteuid()!=0 or len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Explicit immutable TEST runtime required')
    source=Path('/opt/kudavpn-website-review')/('site-release-'+revision[:12])
    if (source/'.kuda-revision').read_text().strip()!=revision:raise ValueError('Unknown source')
    spec=importlib.util.spec_from_file_location('site_guard',source/'deploy/website-review/update_site.py')
    guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    run=guard.run
    if Path(run(['systemctl','show','-P','WorkingDirectory',BOT])).resolve()!=OLD:
        raise ValueError('Unexpected pilot runtime')
    if run(['systemctl','show','-P','User',BOT])!='kudavpn_trial_bot':
        raise ValueError('Existing isolated bot identity required')
    pid=run(['systemctl','show','-P','MainPID',BOT])
    env=dict(v.split('=',1) for v in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in v)
    url=urlsplit(env['DATABASE_URL'])
    if (url.hostname!='127.0.0.1' or url.path!='/kudavpn_telegram_pilot'
            or env.get('PILOT_PREMIUM_ACCOUNT_ENABLED')!='1' or env.get('PILOT_WELCOME_ENABLED')!='1'):
        raise ValueError('Isolated Premium bot required')
    owner=json.loads(Path('/etc/kudavpn-website-review/owner.json').read_text())['owner_id']
    if int(env['PILOT_TRIAL_OWNER_ID'])!=owner:raise ValueError('Canonical owner mismatch')
    port=int(env.get('HEALTH_PORT','8080'))
    if not 1024<=port<=65535:raise ValueError('Dedicated pilot health port required')
    health=lambda:bot_ready(run,port)
    guard.wait_ready(probe=health)
    validate_overlay(OLD,source)
    if (DROPIN.is_symlink() or not DROPIN.is_file()
            or DROPIN.read_text()!='[Service]\nWorkingDirectory='+str(OLD)+'\n'):
        raise ValueError('Expected managed test UI override required')
    backup=Path('/var/backups/kudavpn-telegram-pilot')/('menu-'+revision[:12])
    backup.mkdir(mode=0o700,parents=True)
    # Reuse protected collection but allow exactly the requested bot PID/unit
    # to change. All four Xray entries remain at the start of the tuple.
    bp=Path('/opt/kudavpn-website-review/current/deploy/website-review/baseline.py')
    bs=importlib.util.spec_from_file_location('protected_bot_ui',bp)
    baseline=importlib.util.module_from_spec(bs);bs.loader.exec_module(baseline)
    baseline.UNITS=tuple(unit for unit in baseline.UNITS if unit!=BOT)
    guard.EXTRA=guard.EXTRA+(guard.SITE,)
    before=baseline.collect();extra=guard.extras();financial=guard.digest()
    guard.write(backup/'protected.json',json.dumps(before))
    guard.write(backup/'extra.json',json.dumps(extra))
    guard.write(backup/'old-directory',str(OLD))
    shutil.copy2(DROPIN,backup/'previous-dropin');(backup/'previous-dropin').chmod(0o600)
    unitfiles=[run(['systemctl','show','-P','FragmentPath',BOT]),*run(['systemctl','show','-P','DropInPaths',BOT]).split()]
    for i,name in enumerate(unitfiles):
        target=backup/('unit-'+str(i));shutil.copy2(name,target);target.chmod(0o600)
    state=Path(env['PILOT_WELCOME_STATE_FILE'])
    if not state.is_file() or not str(state).startswith(str(ROOT/'private-welcome')+'/'):
        raise ValueError('Existing private onboarding state required')
    with sqlite3.connect('file:'+str(state)+'?mode=ro',uri=True) as source_db:
        with sqlite3.connect(backup/'onboarding.sqlite') as target:source_db.backup(target)
    (backup/'onboarding.sqlite').chmod(0o600)
    with (backup/'canonical.dump').open('xb') as stream:
        result=subprocess.run(['runuser','-u','postgres','--','pg_dump','-Fc','kudavpn_telegram_pilot'],
                              stdout=stream,stderr=subprocess.PIPE,timeout=40)
    if result.returncode:raise ValueError('Backup failed')
    run(['pg_restore','--list',str(backup/'canonical.dump')])
    run(['pg_restore','--file=/dev/null',str(backup/'canonical.dump')])
    clone=ROOT/('app-purchase-menu-'+revision[:12])
    shutil.copytree(OLD,clone,ignore=shutil.ignore_patterns('__pycache__'))
    for name in FILES:shutil.copy2(source/name,clone/name)
    if code_digest(OLD)!=code_digest(clone):raise ValueError('Non-UI code changed')
    probe=('import sys;sys.path.insert(0,'+repr(str(clone))+');'
           'from bot.screens import home,tariffs,connect;from bot.handlers.start import create_router;'
           'from bot.premium_emoji import EMOJI,decorate,decorate_reply;from bot.keyboards.reply import premium_reply_keyboard;'
           'assert len(EMOJI)==30;assert all(b.icon_custom_emoji_id for row in decorate_reply(premium_reply_keyboard()).keyboard for b in row);'
           'screen=home(premium_account=True);create_router();'
           'assert all(b.icon_custom_emoji_id for row in decorate(screen).keyboard.inline_keyboard for b in row);'
           'assert [b.callback_data for row in screen.keyboard.inline_keyboard for b in row]'
           '==["nav:tariffs","nav:account","nav:connect","nav:support","nav:info"];'
           'assert [row[0].style for row in tariffs(premium_account=True).keyboard.inline_keyboard[:3]]'
           '==["primary","success","primary"];'
           'assert "private-fixture" not in connect("https://example.invalid/private-fixture",premium_account=True).text')
    run(['runuser','-u','kudavpn_trial_bot','--',str(ROOT/'venv/bin/python'),'-c',probe],timeout=30)
    rollback='#!/bin/sh\nset -eu\ncp -p '+str(backup/'previous-dropin')+' '+str(DROPIN)+'\nsystemctl daemon-reload\nsystemctl restart '+BOT+'\n'
    guard.write(backup/'rollback.sh',rollback,0o700);run(['sh','-n',str(backup/'rollback.sh')])
    changed=False
    try:
        changed=True
        DROPIN.write_text('[Service]\nWorkingDirectory='+str(clone)+'\n');DROPIN.chmod(0o644)
        run(['systemd-analyze','verify','/etc/systemd/system/'+BOT])
        run(['systemctl','daemon-reload']);run(['systemctl','restart',BOT],timeout=60)
        guard.wait_ready(probe=health)
        if (baseline.collect()!=before or guard.extras()!=extra or guard.digest()!=financial
                or run(['systemctl','show','-P','WorkingDirectory',BOT])!=str(clone)):
            raise ValueError('Protected state changed')
        guard.write(backup/'activated','ready')
        print('test_bot_menu=ready ui_files_only=true canonical_data=unchanged protected_services=unchanged')
    except Exception:
        if changed:
            restore_dropin(backup)
            run(['systemctl','daemon-reload']);run(['systemctl','restart',BOT],timeout=60)
            guard.wait_ready(probe=health)
        raise


if __name__=='__main__':
    try:
        os.umask(0o077)
        if len(sys.argv)!=2:raise ValueError('Revision required')
        main(sys.argv[1])
    except Exception:
        raise SystemExit('TEST bot UI update stopped; private details withheld') from None
