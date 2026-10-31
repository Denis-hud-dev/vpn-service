"""Run via approved SSH stdin; installs NEW backup components only.

No package upgrades, working-service restarts, database role/row changes or
firewall changes. A later explicit call installs the peer's public upload key.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess as sp
import sys
import tarfile
import urllib.request

ARCHIVE_SHA='cbe24006683f8eb669266162894b9a522a1af52f2665fbc63a4bb032ed26ac10'
URL='https://github.com/FiloSottile/age/releases/download/v1.3.2/age-v1.3.2-linux-amd64.tar.gz'
BASE=Path('/var/lib/kudavpn-backup')
LIB=Path('/usr/local/lib/kudavpn-backup')
ETC=Path('/etc/kudavpn-backup')


def run(args):
    result=sp.run(args,stdout=sp.PIPE,stderr=sp.PIPE,timeout=60)
    if result.returncode:raise RuntimeError('provision_subprocess_failed')
    return result.stdout


def write(path,data,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as file:
        os.fchmod(file.fileno(),mode);file.write(data);file.flush();os.fsync(file.fileno())


def baseline(config):
    result={'units':{},'containers':{},'configs':{}}
    for name in config['units']:
        result['units'][name]=run(['systemctl','show',name,'--property=MainPID,ActiveEnterTimestamp,ActiveState,UnitFileState']).decode()
    if config['host']=='digitalocean':
        for name in ['kudavpn-premium-bot-1','kudavpn-premium-postgres-1','kudavpn-xray-xray-1']:
            j=json.loads(run(['docker','inspect',name]))[0]
            result['containers'][name]={'started':j['State']['StartedAt'],'running':j['State']['Running'],'image':j['Image']}
    for path in config['protect']:
        p=Path(path)
        if not p.is_file():raise RuntimeError('missing_protected_config')
        result['configs'][path]=hashlib.sha256(p.read_bytes()).hexdigest()
    # ss includes volatile queues/fd numbers, especially across daemon-reload.
    # Protect addresses/ports, not that unstable presentation.
    result['tcp_listeners']=sorted(line.split()[3] for line in run(['ss','-H','-ltnp']).decode().splitlines())
    return result


def native_postgres_paths():
    query="SELECT json_build_object('cert',current_setting('ssl_cert_file'),'key',current_setting('ssl_key_file'),'data',current_setting('data_directory'))"
    settings=json.loads(run(['runuser','-u','postgres','--','env','PGOPTIONS=-c default_transaction_read_only=on','psql','-X','-A','-t','-d','postgres','-c',query]))
    paths=['/etc/postgresql','/etc/postgresql-common']
    for field in ('cert','key'):
        p=Path(settings[field])
        if not p.is_absolute():p=Path(settings['data'])/p
        if p.is_file():paths.append(str(p))
    if not all(Path(p).exists() for p in paths):raise RuntimeError('native_postgres_config_missing')
    return paths


def service(command,credentials=False):
    extra='LoadCredentialEncrypted=upload-key:/etc/credstore.encrypted/kudavpn-backup-upload\n' if credentials else ''
    return ('[Unit]\nDescription=KudaVPN isolated backup '+command+'\nAfter=network-online.target docker.service postgresql@16-main.service\n'
            '[Service]\nType=oneshot\nExecStart=/usr/bin/python3 /usr/local/lib/kudavpn-backup/backup.py '+command+'\n'
            'UMask=0077\nNice=15\nCPUQuota=25%\nMemoryMax=384M\nIOWeight=10\nIOSchedulingClass=idle\n'
            'TimeoutStartSec=20min\nNoNewPrivileges=yes\nPrivateTmp=yes\nProtectSystem=strict\nProtectHome=read-only\n'
            'ReadWritePaths=/var/lib/kudavpn-backup\nLimitCORE=0\n'+extra).encode()


def provision(payload):
    config=payload['config']
    if os.geteuid()!=0 or os.uname().nodename!=config['expected_hostname']:raise RuntimeError('wrong_host')
    if BASE.exists() or LIB.exists() or ETC.exists():raise RuntimeError('already_provisioned_review_required')
    disk=shutil.disk_usage('/')
    if disk.free<max(2*1024**3,disk.total//5):raise RuntimeError('disk_low')
    unitroot=Path('/etc/systemd/system')
    names=['kudavpn-backup.service','kudavpn-backup.timer','kudavpn-backup-seal.service','kudavpn-backup-seal.timer','kudavpn-backup-verify.service','kudavpn-backup-verify.timer','kudavpn-backup-retry.service','kudavpn-backup-retry.timer']
    if config['host']=='justhost':names+=['kudavpn-backup-health.service','kudavpn-backup-health.timer']
    if config['host']=='digitalocean':names+=['kudavpn-backup-lab-cleanup.service','kudavpn-backup-lab-cleanup.timer']
    if any((unitroot/n).exists() for n in names):raise RuntimeError('unit_collision')
    before=baseline(config)
    if config['host']=='justhost':config['capture']=list(dict.fromkeys(config['capture']+native_postgres_paths()))
    with urllib.request.urlopen(URL,timeout=90) as response:data=response.read(25*1024**2)
    if hashlib.sha256(data).hexdigest()!=ARCHIVE_SHA:raise RuntimeError('official_age_checksum')
    LIB.mkdir(mode=0o755);ETC.mkdir(mode=0o700);BASE.mkdir(mode=0o755)
    (BASE/'local').mkdir(mode=0o700)
    for root in ['inbox','vault','receipts','ssh-homes']:(BASE/root).mkdir(mode=0o755)
    write(BASE/'baseline.json',json.dumps(before,sort_keys=True).encode())
    with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as tar:
        for name in ['age','age-keygen']:
            member=tar.getmember('age/'+name)
            if not member.isfile() or member.size>25*1024**2:raise RuntimeError('invalid_binary')
            write(LIB/name,tar.extractfile(member).read(),0o755)
    config['age']=str(LIB/'age');config['age_sha256']=hashlib.sha256((LIB/'age').read_bytes()).hexdigest()
    write(LIB/'backup.py',payload['source'].encode(),0o644)
    write(LIB/'restore_lab.py',payload['restore_lab_source'].encode(),0o644)
    write(LIB/'pause-backups.sh',payload['pause_source'].encode(),0o700)
    write(ETC/'config.json',json.dumps(config,sort_keys=True).encode())
    write(ETC/'known_hosts',payload['known_hosts'].encode())
    peer=config['remote_host'];user=config['remote_user_on_this_host']
    home=BASE/'ssh-homes'/user;home.mkdir(mode=0o755)
    try:pwd.getpwnam(user);raise RuntimeError('user_collision')
    except KeyError:pass
    run(['useradd','--system','--user-group','--home-dir',str(home),'--shell','/bin/sh',user])
    account=pwd.getpwnam(user)
    for root in ['inbox','vault','receipts']:
        p=BASE/root/peer;p.mkdir(mode=0o700 if root=='inbox' else 0o750)
        os.chown(p,account.pw_uid if root=='inbox' else 0,account.pw_gid)
    (home/'.ssh').mkdir(mode=0o755)
    # Upload private key exists only on tmpfs until systemd encrypts it.
    stage=Path('/run/kudavpn-backup-bootstrap');stage.mkdir(mode=0o700)
    private=stage/'upload';run(['ssh-keygen','-q','-t','ed25519','-N','','-C','kudavpn-backup-'+config['host'],'-f',str(private)])
    cred=Path('/etc/credstore.encrypted');cred.mkdir(mode=0o700,exist_ok=True)
    if (cred/'kudavpn-backup-upload').exists():raise RuntimeError('credential_collision')
    try:
        run(['systemd-creds','encrypt','--with-key=host','--name=upload-key',str(private),str(cred/'kudavpn-backup-upload')])
        public=private.with_suffix('.pub').read_text().strip()
    finally:
        private.unlink(missing_ok=True);private.with_suffix('.pub').unlink(missing_ok=True);stage.rmdir()
    (cred/'kudavpn-backup-upload').chmod(0o600)
    units={
        'kudavpn-backup.service':service('backup',True),
        'kudavpn-backup-retry.service':service('retry',True),
        'kudavpn-backup-retry.timer':b'[Unit]\nDescription=Retry pending offsite transfer hourly\n[Timer]\nOnBootSec=20min\nOnUnitInactiveSec=1h\nPersistent=true\n[Install]\nWantedBy=timers.target\n',
        'kudavpn-backup.timer':('[Unit]\nDescription=Daily encrypted KudaVPN backup\n[Timer]\nOnCalendar=*-*-* '+config['daily_time']+' UTC\nRandomizedDelaySec=5min\nPersistent=true\n[Install]\nWantedBy=timers.target\n').encode(),
        'kudavpn-backup-seal.service':service('seal'),
        'kudavpn-backup-seal.timer':b'[Unit]\nDescription=Bounded ciphertext sealing, no path-event bursts\n[Timer]\nOnBootSec=1s\nOnUnitInactiveSec=10s\nAccuracySec=1s\n[Install]\nWantedBy=timers.target\n',
        'kudavpn-backup-verify.service':service('verify'),
        'kudavpn-backup-verify.timer':b'[Unit]\nDescription=Backup integrity every15minutes\n[Timer]\nOnBootSec=5min\nOnUnitActiveSec=15min\nPersistent=true\n[Install]\nWantedBy=timers.target\n',
    }
    if config['host']=='justhost':
        units['kudavpn-backup-health.service']=service('health',True)
        units['kudavpn-backup-health.timer']=b'[Unit]\nDescription=Owner backup health observer\n[Timer]\nOnBootSec=10min\nOnUnitActiveSec=15min\nPersistent=true\n[Install]\nWantedBy=timers.target\n'
    else:
        units['kudavpn-backup-lab-cleanup.service']=service('verify').replace(b'backup.py verify',b'restore_lab.py cleanup')
        units['kudavpn-backup-lab-cleanup.timer']=b'[Unit]\nDescription=Remove only expired isolated restore labs\n[Timer]\nOnBootSec=10min\nOnUnitActiveSec=15min\n[Install]\nWantedBy=timers.target\n'
    for name,data in units.items():write(unitroot/name,data,0o644)
    run(['systemd-analyze','verify',*[str(unitroot/n) for n in names]])
    run(['systemctl','daemon-reload'])
    run(['systemctl','enable','--now','kudavpn-backup-seal.timer'])
    after=baseline(config)
    if before!=after:raise RuntimeError('protected_baseline_changed')
    print(json.dumps({'host':config['host'],'public_upload_key':public,'age_sha256':config['age_sha256'],'protected_baseline_unchanged':True}))


def authorize(payload):
    config=json.loads((ETC/'config.json').read_text());peer=config['remote_host'];user=config['remote_user_on_this_host']
    key=payload['public_upload_key']
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+ kudavpn-backup-(digitalocean|justhost)',key):raise RuntimeError('invalid_public_key')
    if key.split()[-1]!='kudavpn-backup-'+peer:raise RuntimeError('wrong_peer_key')
    line=('from="'+config['remote_ip']+'",restrict,command="/usr/bin/python3 /usr/local/lib/kudavpn-backup/backup.py receive '+peer+'" '+key+'\n').encode()
    path=BASE/'ssh-homes'/user/'.ssh/authorized_keys'
    write(path,line,0o644)
    print(json.dumps({'host':config['host'],'peer_authorized':peer,'restricted':True}))


def resume_reviewed(payload):
    config=json.loads((ETC/'config.json').read_text())
    before=json.loads((BASE/'baseline.json').read_text());now=baseline(config)
    for key in ('units','containers','configs'):
        if before[key]!=now[key]:raise RuntimeError('protected_baseline_changed')
    if now['tcp_listeners']!=sorted(payload['reviewed_tcp_listeners']):raise RuntimeError('listeners_changed')
    write(BASE/'baseline-normalized.json',json.dumps(now,sort_keys=True).encode())
    public=run(['systemd-run','--quiet','--wait','--collect','--pipe','-p','LoadCredentialEncrypted=upload-key:/etc/credstore.encrypted/kudavpn-backup-upload',
                '/bin/sh','-c','exec ssh-keygen -y -f "$CREDENTIALS_DIRECTORY/upload-key"']).decode().strip()
    public=' '.join(public.split()[:2])+' kudavpn-backup-'+config['host']
    if now!=baseline(config):raise RuntimeError('protected_baseline_changed')
    print(json.dumps({'host':config['host'],'public_upload_key':public,'age_sha256':config['age_sha256'],'protected_baseline_unchanged':True}))


if __name__=='__main__':
    try:
        payload=json.load(sys.stdin)
        if payload.get('action')=='authorize':authorize(payload)
        elif payload.get('action')=='resume_reviewed':resume_reviewed(payload)
        else:provision(payload)
    except Exception as exc:
        code=str(exc) if isinstance(exc,RuntimeError) and re.fullmatch('[a-z_]+',str(exc)) else type(exc).__name__
        print('backup_provision_failed: '+code,file=sys.stderr);raise SystemExit(1)
