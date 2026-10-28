"""Pin only the owner review website to an immutable release; no shared switch.

Root-only backup first, no migrations/provider POSTs. Existing bridge/exporter,
pending poller, bots, Monitor and VPN retain their code paths and processes.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

BASE = Path('/opt/kudavpn-website-review')
CONFIG = Path('/etc/kudavpn-website-review')
BACKUPS = Path('/var/backups/kudavpn-website-review')
SITE = 'kudavpn-website-review.service'
DROPIN = Path('/etc/systemd/system') / (SITE + '.d') / '90-review-release.conf'
PYTHON = str(BASE/'venv/bin/python')
EXTRA = ('kudavpn-site-payments.service', 'kudavpn-website-export.timer',
         'kudavpn-monitor.service', 'kudavpn-monitor-collect.timer')


def run(args, *, timeout=40):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Deployment command failed; private details withheld')
    return result.stdout.strip()


def paths(revision):
    if not isinstance(revision, str) or len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Exact revision required')
    return BASE/('site-release-'+revision[:12]), BACKUPS/('site-'+revision[:12])


def write(path, value, mode=0o600):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)


def digest():
    tables = ('users', 'subscriptions', 'payments', 'subscription_history', 'subscription_tokens')
    sql = ' UNION ALL '.join("SELECT '"+table+"' || row_to_json(t)::text FROM "+table+' t' for table in tables)
    # Node lease revisions can legitimately renew during a deployment. Pin the
    # profile identity, not its normal background synchronization timestamp.
    sql += " UNION ALL SELECT 'node_access' || row_to_json(t)::text FROM (SELECT id,subscription_id,node_id,credential FROM node_access) t"
    raw = run(['runuser','-u','postgres','--','psql','-X','-At','-d','kudavpn_telegram_pilot','-c',sql])
    return hashlib.sha256('\n'.join(sorted(raw.splitlines())).encode()).hexdigest()


def extras():
    result = {}
    for unit in EXTRA:
        raw = run(['systemctl','show',unit,'-p','ActiveState','-p','MainPID','-p','FragmentPath','-p','DropInPaths'])
        values = dict(row.split('=',1) for row in raw.splitlines() if '=' in row)
        if values['ActiveState'] != 'active':
            raise ValueError('Protected dependency unavailable')
        # Timers have no process/MainPID property. Running services still must
        # have a live PID; do not treat a missing service process as healthy.
        pid = values.get('MainPID','0')
        if not unit.endswith('.timer') and (not pid.isdecimal() or int(pid)<2):
            raise ValueError('Protected dependency process unavailable')
        files = [values['FragmentPath'], *values['DropInPaths'].split()]
        result[unit] = {'pid':pid, 'state':values['ActiveState'],
            'files':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files}}
    return result


def protected(mode, backup):
    old = json.loads((backup/'before.json').read_text())['shared_release']
    run([PYTHON, str(Path(old)/'deploy/website-review/baseline.py'), mode, str(backup/'protected.json')])


def ready():
    raw = run(['curl','--silent','--show-error','--fail','--max-time','5',
        '--resolve','r1276248.s2578.vm.mydomain.zone:20443:127.0.0.1',
        'https://r1276248.s2578.vm.mydomain.zone:20443/ready'], timeout=7)
    return json.loads(raw) == {'ready':True,'read_only':False}


def wait_ready(*, clock=time.monotonic, sleep=time.sleep, probe=ready):
    deadline = clock() + 90
    while clock() < deadline:
        try:
            if probe(): return
        except Exception:
            pass
        sleep(2)
    raise RuntimeError('Website readiness deadline exceeded')


def prepare(revision):
    _, backup = paths(revision)
    if run(['systemctl','is-active',SITE]) != 'active' or not ready():
        raise ValueError('Existing payment website must be healthy')
    if run(['systemctl','show','-P','DropInPaths',SITE]) not in {'',str(DROPIN)}:
        raise ValueError('Unexpected website override')
    shared = (BASE/'current').resolve(strict=True)
    if shared.parent != BASE or shared.name != 'release-faca30b':
        raise ValueError('Unexpected shared runtime; do not replace it')
    if (Path('/etc/kudavpn-site-payments/payments.json').stat().st_mode & 0o077):
        raise ValueError('Private sandbox configuration required')
    values = json.loads(Path('/etc/kudavpn-site-payments/payments.json').read_text())
    spec=importlib.util.spec_from_file_location('bridge_guard',shared/'bot/subscription_api/payment_bridge.py')
    # The trusted running release validates exact test DB/shop/key environment.
    sys.path.insert(0,str(shared))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.validate_configuration(values); values.clear()
    if run(['runuser','-u','postgres','--','psql','-X','-At','-d','kudavpn_website_test',
            '-c','SELECT version_num FROM alembic_version']) != '0009_website':
        raise ValueError('No auth database migration is allowed')
    if run(['runuser','-u','postgres','--','psql','-X','-At','-d','kudavpn_telegram_pilot',
            '-c','SELECT version_num FROM alembic_version']) != '0008_premium_notices':
        raise ValueError('Canonical test schema must remain unchanged')
    memory = int(next(row.split()[1] for row in Path('/proc/meminfo').read_text().splitlines() if row.startswith('MemAvailable:')))
    if memory < 200*1024 or shutil.disk_usage(BASE).free < 200*1024*1024:
        raise ValueError('Insufficient spare resources')
    before = {'shared_release':str(shared), 'working_directory':run(['systemctl','show','-P','WorkingDirectory',SITE]),
              'dropin_existed':DROPIN.exists(), 'extras':extras(), 'source_digest':digest()}
    backup.mkdir(mode=0o700)
    write(backup/'before.json',json.dumps(before))
    protected('save',backup)
    shutil.copytree(CONFIG,backup/'site-config')
    (backup/'site-config').chmod(0o700)
    shutil.copy2('/etc/systemd/system/'+SITE,backup/SITE)
    if DROPIN.exists(): shutil.copy2(DROPIN,backup/'previous-dropin')
    # Backups do not pause or migrate either database. No stale dump is ever
    # restored automatically over payments/authentication arriving meanwhile.
    for name in ('kudavpn_website_test','kudavpn_telegram_pilot'):
        with (backup/(name+'.dump')).open('xb') as stream:
            result=subprocess.run(['runuser','-u','postgres','--','pg_dump','-Fc',name],
                                  stdout=stream,stderr=subprocess.PIPE,timeout=40)
        if result.returncode: raise RuntimeError('Private backup failed')
        (backup/(name+'.dump')).chmod(0o600)
        run(['pg_restore','--list',str(backup/(name+'.dump'))])
        run(['pg_restore','--file=/dev/null',str(backup/(name+'.dump'))])
    snapshot=json.loads(Path('/var/lib/kudavpn-website-export/snapshot.json').read_text())
    write(backup/'feed.sha256',hashlib.sha256(snapshot['account']['feed_url'].encode()).hexdigest())
    rollback = '#!/bin/sh\nset -eu\nexec '+PYTHON+' '+str(backup/'update_site.py')+' rollback '+revision+'\n'
    shutil.copy2(Path(__file__),backup/'update_site.py')
    write(backup/'rollback.sh',rollback,0o700)
    run(['sh','-n',str(backup/'rollback.sh')])
    # A rollback round-trip of the current drop-in bytes is rehearsed privately,
    # never by restarting the live VPN or replacing the working database.
    rehearsal=backup/'rollback-rehearsal.conf'
    write(rehearsal,'[Service]\nWorkingDirectory=/invalid-rehearsal\n')
    restore_dropin(backup,target=rehearsal)
    if (before['dropin_existed'] and rehearsal.read_bytes()!=(backup/'previous-dropin').read_bytes()
            or not before['dropin_existed'] and rehearsal.exists()):
        raise ValueError('Rollback configuration rehearsal failed')
    write(backup/'rollback-configuration-tested',json.dumps({'previous_dropin':before['dropin_existed'],
        'working_directory':before['working_directory']}))
    write(backup/'prepared','ready')
    print('site_backup=validated rollback_plan=validated',flush=True)


def verify(revision, *, source_unchanged=True):
    _, backup = paths(revision)
    before=json.loads((backup/'before.json').read_text())
    protected('verify',backup)
    if extras()!=before['extras'] or str((BASE/'current').resolve())!=before['shared_release']:
        raise ValueError('Protected dependency changed')
    if (CONFIG/'site.env').read_bytes()!=(backup/'site-config/site.env').read_bytes():
        raise ValueError('Website environment changed')
    if source_unchanged and digest()!=before['source_digest']:
        raise ValueError('Canonical data changed during site update')
    snapshot=json.loads(Path('/var/lib/kudavpn-website-export/snapshot.json').read_text())
    if hashlib.sha256(snapshot['account']['feed_url'].encode()).hexdigest()!=(backup/'feed.sha256').read_text():
        raise ValueError('Existing Happ URL changed')
    wait_ready()


def restore_dropin(backup, *, target=None):
    target=DROPIN if target is None else target
    before=json.loads((backup/'before.json').read_text())
    if before['dropin_existed']:
        shutil.copy2(backup/'previous-dropin',target)
    else:
        target.unlink(missing_ok=True)


def rollback(revision):
    _, backup=paths(revision)
    restore_dropin(backup)
    run(['systemctl','daemon-reload'])
    run(['systemctl','restart',SITE],timeout=60)
    wait_ready()
    # Legitimate concurrent payments must survive rollback unchanged.
    verify(revision,source_unchanged=False)
    before=json.loads((backup/'before.json').read_text())
    if run(['systemctl','show','-P','WorkingDirectory',SITE])!=before['working_directory']:
        raise ValueError('Rollback did not restore the prior website path')
    print('site_rollback=ready protected_services=unchanged',flush=True)


def activate(revision):
    release,backup=paths(revision)
    if (backup/'prepared').read_text()!='ready' or (release/'.kuda-revision').read_text().strip()!=revision:
        raise ValueError('Verified backup and immutable source required')
    verify(revision)
    shared=Path(json.loads((backup/'before.json').read_text())['shared_release'])
    if (release/'bot/requirements.txt').read_bytes()!=(shared/'bot/requirements.txt').read_bytes():
        raise ValueError('No dependency changes are allowed')
    for path in (release,*release.rglob('*')):
        if path.is_symlink(): raise ValueError('Unexpected runtime symlink')
        path.chmod(0o755 if path.is_dir() else 0o644)
    changed=False
    try:
        DROPIN.parent.mkdir(mode=0o755,exist_ok=True)
        changed=True
        DROPIN.write_text('[Service]\nWorkingDirectory='+str(release)+'\n')
        DROPIN.chmod(0o644)
        run(['systemd-analyze','verify','/etc/systemd/system/'+SITE])
        run(['systemctl','daemon-reload'])
        run(['systemctl','restart',SITE],timeout=60)
        wait_ready()
        if run(['systemctl','show','-P','WorkingDirectory',SITE])!=str(release):
            raise ValueError('Website code path not applied')
        verify(revision)
        write(backup/'activated','ready')
        print('site_update=ready protected_services=unchanged source_data=unchanged',flush=True)
    except Exception:
        if changed: rollback(revision)
        raise


if __name__=='__main__':
    try:
        os.umask(0o077)
        if os.geteuid()!=0 or len(sys.argv)!=3 or sys.argv[1] not in {'prepare','activate','verify','rollback'}:
            raise ValueError('Explicit root deployment mode required')
        globals()[sys.argv[1]](sys.argv[2])
    except Exception:
        raise SystemExit('Website update stopped; private diagnostic details withheld') from None
