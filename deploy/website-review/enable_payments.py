"""Add sandbox payments to the working owner review; never restart protected units.

Run from a verified immutable release as root. No migrations or provider POSTs.
Backups stay root-only. Rollback restores the previous working website/code/env,
not a stale database dump (a payment could have legitimately arrived meanwhile).
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

BASE=Path('/opt/kudavpn-website-review')
CONFIG=Path('/etc/kudavpn-website-review')
PAYMENT_CONFIG=Path('/etc/kudavpn-site-payments')
BACKUP=Path('/var/backups/kudavpn-website-review/20261027-payments')
SITE='kudavpn-website-review.service'
BRIDGE='kudavpn-site-payments.service'
PYTHON=str(BASE/'venv/bin/python')


def run(args,*,input=None,timeout=60):
    result=subprocess.run(args,input=input,capture_output=True,text=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError('Deployment step failed; private details withheld')
    return result.stdout.strip()


def write(path,value):
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(value)


def source_env(unit):
    pid=run(['systemctl','show','-P','MainPID',unit])
    if not pid.isdecimal() or int(pid)<2:raise ValueError('Test service unavailable')
    return dict(row.split('=',1) for row in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in row)


def baseline(mode):
    run([PYTHON,str(BASE/'current/deploy/website-review/baseline.py'),mode,str(BACKUP/'protected.json')])


def dump(database):
    path=BACKUP/(database+'.dump')
    with path.open('xb') as stream:
        proc=subprocess.run(['runuser','-u','postgres','--','pg_dump','-Fc',database],
                            stdout=stream,stderr=subprocess.PIPE,timeout=40)
    path.chmod(0o600)
    if proc.returncode:raise RuntimeError('Private backup failed')
    run(['pg_restore','--list',str(path)])
    run(['pg_restore','--file=/dev/null',str(path)])


def rows_digest(database):
    statements=' UNION ALL '.join("SELECT '"+table+"' || row_to_json(t)::text FROM "+table+' t'
                                  for table in ('users','subscriptions','payments','subscription_tokens'))
    raw=run(['runuser','-u','postgres','--','psql','-X','-At','-d',database,'-c',statements])
    return hashlib.sha256('\n'.join(sorted(raw.splitlines())).encode()).hexdigest()


def ready():
    return json.loads(run(['curl','--silent','--show-error','--fail','--max-time','5',
        '--resolve','r1276248.s2578.vm.mydomain.zone:20443:127.0.0.1',
        'https://r1276248.s2578.vm.mydomain.zone:20443/ready']))


def main():
    stage='preflight'
    changed=False
    try:
        os.umask(0o077)
        if os.geteuid()!=0:raise ValueError()
        revision=sys.argv[1]
        if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):raise ValueError()
        release=BASE/('release-'+revision[:7])
        old=(BASE/'current').resolve(strict=True)
        if old.parent!=BASE or not old.name.startswith('release-') or old==release:raise ValueError()
        if (not release.is_dir() or not (release/'bot/subscription_api/payment_bridge.py').is_file()
                or BACKUP.exists() or PAYMENT_CONFIG.exists()
                or Path('/etc/systemd/system/'+BRIDGE).exists()):raise ValueError()
        for service in (SITE,'kudavpn-website-export.timer','kudavpn-test-checkout.service'):
            if run(['systemctl','is-active',service])!='active':raise ValueError()
        if ready()!= {'ready':True,'read_only':True}:raise ValueError('Working read-only site required')
        env=source_env('kudavpn-test-checkout.service')
        owner=json.loads((CONFIG/'owner.json').read_text())['owner_id']
        if int(env['PILOT_TRIAL_OWNER_ID'])!=owner:raise ValueError()
        values={'owner_id':owner,'database_url':env['DATABASE_URL'],
                'shop_id':Path(env['PILOT_YOOKASSA_TEST_SHOP_ID_FILE']).read_text().strip(),
                'secret_key':Path(env['PILOT_YOOKASSA_TEST_SECRET_FILE']).read_text().strip()}
        # Import public validation only; no service, DB, HTTP or payment created.
        sys.path.insert(0,str(release))
        from bot.subscription_api.payment_bridge import validate_configuration
        validate_configuration(values)
        if run(['runuser','-u','postgres','--','psql','-X','-At','-d','kudavpn_telegram_pilot','-c','SELECT version_num FROM alembic_version'])!='0008_premium_notices':raise ValueError()
        BACKUP.mkdir(mode=0o700)
        baseline('save')
        shutil.copy2(CONFIG/'site.env',BACKUP/'site.env')
        shutil.copy2('/etc/systemd/system/'+SITE,BACKUP/SITE)
        write(BACKUP/'previous-release',str(old))
        dump('kudavpn_telegram_pilot');dump('kudavpn_website_test')
        digest=rows_digest('kudavpn_telegram_pilot')
        write(BACKUP/'source.digest',digest)
        rollback='''#!/bin/sh
set -eu
systemctl disable --now kudavpn-site-payments.service || true
cp /var/backups/kudavpn-website-review/20261027-payments/site.env /etc/kudavpn-website-review/site.env
old=$(cat /var/backups/kudavpn-website-review/20261027-payments/previous-release)
ln -sfn "$old" /opt/kudavpn-website-review/current
systemctl restart kudavpn-website-review.service
/opt/kudavpn-website-review/venv/bin/python "$old/deploy/website-review/baseline.py" verify /var/backups/kudavpn-website-review/20261027-payments/protected.json
'''
        write(BACKUP/'rollback.sh',rollback)
        run(['sh','-n',str(BACKUP/'rollback.sh')])
        stage='isolated_configuration'
        # Immutable public runtime only; credentials are never in this release.
        for path in (release,*release.rglob('*')):
            if path.is_symlink():continue
            if path.is_dir():path.chmod(0o755)
            elif path.is_file():path.chmod(0o644)
        run(['useradd','--system','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin',
             '--gid','kudavpn_website_test','kudavpn_site_payments'])
        PAYMENT_CONFIG.mkdir(mode=0o700)
        write(PAYMENT_CONFIG/'payments.json',json.dumps(values))
        values.clear();env.clear()
        shutil.copyfile(release/'deploy/website-review/kudavpn-site-payments.service','/etc/systemd/system/'+BRIDGE)
        Path('/etc/systemd/system/'+BRIDGE).chmod(0o644)
        # Return to the already working read-only site if any subsequent step fails.
        changed=True
        current=CONFIG/'site.env'
        original=current.read_text()
        if 'SITE_PAYMENTS_MODE=' in original:raise ValueError('Unexpected prior payment configuration')
        current.write_text(original+'SITE_PAYMENTS_MODE="owner-sandbox"\n');current.chmod(0o600)
        (BASE/'current').unlink();(BASE/'current').symlink_to(release)
        run(['systemctl','daemon-reload'])
        run(['systemd-analyze','verify','/etc/systemd/system/'+BRIDGE])
        stage='bridge_readiness'
        run(['systemctl','enable','--now',BRIDGE])
        probe="import asyncio;from bot.services.website_payments import LocalPaymentClient;asyncio.run(LocalPaymentClient().request('ready'));print('local_bridge_ready')"
        for _ in range(45):
            try:
                run(['runuser','-u','kudavpn_website_test','--',PYTHON,'-c',probe],timeout=10)
                break
            except Exception:time.sleep(1)
        else:raise RuntimeError('Bridge failed readiness')
        stage='website_readiness'
        run(['systemctl','restart',SITE])
        for _ in range(45):
            try:
                if ready()=={'ready':True,'read_only':False}:break
            except Exception:pass
            time.sleep(1)
        else:raise RuntimeError('Website failed readiness')
        baseline('verify')
        if rows_digest('kudavpn_telegram_pilot')!=digest:raise RuntimeError('Unexpected canonical mutation during deployment')
        print('owner_sandbox_payments=ready; private_backups=verified; protected_services=unchanged; payments_created=0')
    except Exception:
        if changed:
            try:
                run(['sh',str(BACKUP/'rollback.sh')])
                for _ in range(45):
                    try:
                        if ready()=={'ready':True,'read_only':True}:break
                    except Exception:pass
                    time.sleep(1)
                else:raise RuntimeError()
                print('rollback=previous_working_website_restored')
            except Exception:
                raise SystemExit('Rollback requires attention; protected services were not restarted') from None
        raise SystemExit('Sandbox deployment stopped at '+stage+'; details withheld') from None


if __name__=='__main__':main()
