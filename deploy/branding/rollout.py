"""Root-only visual TEST rollout. No migrations, provider calls or VPN restarts."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
import urllib.request
from urllib.parse import urlsplit

BASE=Path('/var/backups/kudavpn-branding/ca0d10ece2f3')
STAGE_NAMES=('website','feed','checkout','bot')
READ_GUARD=Path('/opt/kudavpn-website-review/site-release-0d45896c9317/deploy/website-review/update_site.py')
READ_GUARD_SHA='53218ba2ef70c85af5a6c39536e6cd6a963e1bfce0bb62a10f0297b013e509d1'
UNITS=('xray.service','kudavpn-xray-alt8443.service','kudavpn-pilot-node.service',
 'kudavpn-pilot-two-node.service','kudavpn-telegram-pilot-bot.service',
 'kudavpn-telegram-pilot-feed.service','kudavpn-test-checkout.service',
 'kudavpn-website-review.service','kudavpn-site-payments.service','kudavpn-monitor.service')
CONFIGS=('/etc/kudavpn-website-review','/etc/kudavpn-telegram-pilot',
 '/etc/kudavpn-site-payments','/etc/kudavpn-monitor','/etc/kudavpn-backup')


def run(args,timeout=45):
    r=subprocess.run(args,capture_output=True,timeout=timeout)
    if r.returncode:raise RuntimeError('subprocess_failed')
    return r.stdout.decode().strip()

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(path,data):
    with Path(path).open('x') as f:json.dump(data,f)
    Path(path).chmod(0o600)

def unit(name):
    raw=run(['systemctl','show',name,'-p','ActiveState','-p','MainPID','-p','WorkingDirectory',
      '-p','FragmentPath','-p','DropInPaths','-p','User','-p','ExecMainStartTimestampMonotonic'])
    return dict(x.split('=',1) for x in raw.splitlines() if '=' in x)

def snapshot():
    data={'units':{},'files':{},'listeners':[]}
    for name in UNITS:
        row=unit(name)
        if row['ActiveState']!='active' or int(row['MainPID'])<2:raise ValueError('service_unavailable')
        data['units'][name]=row
        for path in [row['FragmentPath'],*row['DropInPaths'].split()]:data['files'][path]=sha(path)
        if name in UNITS[:4]:
            argv=Path('/proc/'+row['MainPID']+'/cmdline').read_bytes().decode().split('\0')
            for i,v in enumerate(argv[:-1]):
                if v in ('-c','-config','--config'):data['files'][argv[i+1]]=sha(argv[i+1])
    for folder in CONFIGS:
        for p in Path(folder).rglob('*'):
            if p.is_file():data['files'][str(p)]=sha(p)
    for line in run(['ss','-ltnpH']).splitlines():
        import re
        address=line.split()[3]
        if address.rsplit(':',1)[-1] in {'443','8443','9443','10443','18443','18543','20443'}:
            match=re.search(r'pid=(\d+)',line)
            if not match:raise ValueError('listener_owner_unknown')
            data['listeners'].append([address,match.group(1)])
    data['listeners'].sort()
    return data

def compare(before,after,allowed=None):
    """Only the expressly targeted test HTTPS service may have a new process/CWD."""
    before=json.loads(json.dumps(before));after=json.loads(json.dumps(after))
    if allowed:
        for obj in (before,after):
            row=obj['units'].pop(allowed)
            pid=row['MainPID']
            obj['listeners']=[x for x in obj['listeners'] if x[1]!=pid]
            for p in [row['FragmentPath'],*row['DropInPaths'].split()]:obj['files'].pop(p,None)
    if before!=after:raise ValueError('protected_state_changed')

def data_digest():
    path=READ_GUARD
    if sha(path)!=READ_GUARD_SHA:raise ValueError('readonly_guard_changed')
    spec=importlib.util.spec_from_file_location('existing_readonly_guard',path)
    guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
    return guard.digest()

def test_bot_identity(stage):
    if stage['unit']!='kudavpn-telegram-pilot-bot.service':return
    pid=unit(stage['unit'])['MainPID']
    env=dict(x.split('=',1) for x in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in x)
    token=env['BOT_TOKEN']
    with urllib.request.urlopen('https://api.telegram.org/bot'+token+'/getMe',timeout=10) as response:
        result=json.load(response)
    if not result.get('ok') or result['result']['username'].lower()!='kudavpn_test_bot':raise ValueError('test_bot_identity_mismatch')


def config_for_age():
    config=json.loads(Path('/etc/kudavpn-backup/config.json').read_text())
    if sha(config['age'])!=config['age_sha256']:raise ValueError('encryption_binary_changed')
    return config

def encrypted_backup(folder,paths):
    config=config_for_age();target=folder/'configuration-and-runtime.tar.age'
    with target.open('xb') as out:
        proc=subprocess.Popen([config['age'],'-r',config['recipient']],stdin=subprocess.PIPE,stdout=out,stderr=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=proc.stdin,mode='w|') as archive:
                for p in paths:archive.add(p,arcname=str(p).lstrip('/'),filter=lambda t:None if '__pycache__' in Path(t.name).parts or '.git' in Path(t.name).parts else t)
            proc.stdin.close();proc.stdin=None
            _,err=proc.communicate(timeout=120)
            if proc.returncode:raise ValueError('encryption_failed')
        finally:
            if proc.poll() is None:proc.kill();proc.wait()
    target.chmod(0o600)
    if target.stat().st_size<100 or not target.read_bytes().startswith(b'age-encryption.org/v1\n'):raise ValueError('backup_invalid')
    save(folder/'ciphertext.json',{'sha256':sha(target),'bytes':target.stat().st_size})

def code_hashes(folder):
    return {str(p.relative_to(folder)):sha(p) for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts}

def validate_files(stage,payload):
    old=Path(stage['old'])
    if unit(stage['unit'])['WorkingDirectory']!=str(old):raise ValueError('unexpected_runtime')
    for name,hashes in stage['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('unsafe_path')
        source=payload/name;target=old/name
        if source.is_symlink() or not source.is_file() or sha(source)!=hashes['after']:raise ValueError('payload_mismatch')
        if target.is_symlink():raise ValueError('runtime_symlink')
        if hashes['before'] is None:
            if target.exists():raise ValueError('unexpected_existing_file')
        elif not target.is_file() or sha(target)!=hashes['before']:raise ValueError('runtime_drift')

def atomic_copy(source,target):
    target=Path(target);temporary=target.with_name(target.name+'.kuda-brand-tmp')
    if temporary.exists():raise ValueError('temporary_collision')
    shutil.copy2(source,temporary)
    if target.exists():
        old=target.stat();os.chown(temporary,old.st_uid,old.st_gid);temporary.chmod(old.st_mode&0o777)
    else:temporary.chmod(0o644)
    os.replace(temporary,target)

def ready(name):
    if unit(name)['ActiveState']!='active':return False
    if name=='kudavpn-website-review.service':
        raw=run(['curl','--silent','--show-error','--fail','--max-time','5','--resolve',
          'r1276248.s2578.vm.mydomain.zone:20443:127.0.0.1','https://r1276248.s2578.vm.mydomain.zone:20443/ready'])
        return json.loads(raw)=={'ready':True,'read_only':False}
    # The existing personal feed is read, never logged or regenerated.
    snapshot=json.loads(Path('/var/lib/kudavpn-website-export/snapshot.json').read_text())
    url=snapshot['account']['feed_url']
    if urlsplit(url).scheme!='https':raise ValueError('https_feed_required')
    with urllib.request.urlopen(url,timeout=8) as response:return response.status==200 and bool(response.read())

def wait_ready(name):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        try:
            if ready(name):return
        except Exception:pass
        time.sleep(2)
    raise ValueError('readiness_failed')

def dropin_for(stage):return Path('/etc/systemd/system')/(stage['unit']+'.d')/'99-approved-branding.conf'

def rollback(name,manifest):
    stage=manifest['stages'][name];folder=BASE/name
    if not (folder/'before.json').is_file():raise ValueError('backup_missing')
    if stage['restart']:
        dropin=dropin_for(stage)
        if dropin.exists():
            expected='[Service]\nWorkingDirectory='+str(Path(stage['old']).with_name('brand-ca0d10ece2f3-'+name))+'\n'
            if dropin.read_text()!=expected:raise ValueError('rollback_override_changed')
            dropin.unlink()
        run(['systemctl','daemon-reload']);run(['systemctl','restart',stage['unit']],timeout=60)
        wait_ready(stage['unit'])
        if unit(stage['unit'])['WorkingDirectory']!=stage['old']:raise ValueError('rollback_path_failed')
    else:
        for file in stage['files']:atomic_copy(folder/'original'/file,Path(stage['old'])/file)
    if any(sha(Path(stage['old'])/file)!=hashes['before'] for file,hashes in stage['files'].items() if hashes['before']):raise ValueError('rollback_files_failed')
    save(folder/('rolled-back-'+str(time.time_ns())+'.json'),{'ready':True})

def apply(name,manifest,payload):
    stage=manifest['stages'][name];old=Path(stage['old']);folder=BASE/name
    validate_files(stage,payload/name)
    test_bot_identity(stage)
    if folder.exists() or (stage['restart'] and dropin_for(stage).exists()):raise ValueError('deployment_collision')
    before=snapshot();financial=data_digest()
    wait_ready(stage['unit'])
    if shutil.disk_usage(old).free<500*1024**2:raise ValueError('insufficient_space')
    folder.mkdir(parents=True,mode=0o700)
    save(folder/'before.json',{'protected':before,'data_digest':financial,'old':str(old)})
    row=before['units'][stage['unit']]
    paths=[old,*[Path(p) for p in [row['FragmentPath'],*row['DropInPaths'].split()]],*[Path(p) for p in CONFIGS if Path(p).exists()]]
    encrypted_backup(folder,paths)
    # Keep only non-secret visual originals in plaintext for immediate rollback.
    if not stage['restart']:
        for file in stage['files']:
            dest=folder/'original'/file;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old/file,dest)
    plan='#!/bin/sh\nset -eu\npython3 '+str(Path(__file__).resolve())+' rollback '+name+' '+str(payload)+'\n'
    (folder/'rollback.sh').write_text(plan);(folder/'rollback.sh').chmod(0o700)
    run(['sh','-n',str(folder/'rollback.sh')])
    clone=old.with_name('brand-ca0d10ece2f3-'+name)
    if stage['restart']:
        if clone.exists():raise ValueError('clone_collision')
        shutil.copytree(old,clone,ignore=shutil.ignore_patterns('__pycache__','.git'))
        for file in stage['files']:
            target=clone/file;target.parent.mkdir(parents=True,exist_ok=True);atomic_copy(payload/name/file,target)
        (clone/'bot/assets/brand').chmod(0o755)
        previous=code_hashes(old);updated=code_hashes(clone)
        if {p:h for p,h in previous.items() if p not in stage['files']}!={p:h for p,h in updated.items() if p not in stage['files']}:raise ValueError('nonvisual_code_changed')
        # Import the actual old entrypoint under its existing service identity, no application startup.
        module='bot.subscription_api.website' if name=='website' else 'bot.subscription_api.app'
        python='/opt/kudavpn-website-review/venv/bin/python' if name=='website' else '/opt/kudavpn-telegram-pilot/venv/bin/python'
        run(['runuser','-u',row['User'],'--',python,'-c','import sys;sys.path.insert(0,'+repr(str(clone))+');import '+module])
    compare(before,snapshot())
    if financial!=data_digest():raise ValueError('source_data_changed_before_apply')
    changed=False
    try:
        changed=True
        if stage['restart']:
            dropin=dropin_for(stage);dropin.write_text('[Service]\nWorkingDirectory='+str(clone)+'\n');dropin.chmod(0o644)
            run(['systemctl','daemon-reload']);run(['systemctl','restart',stage['unit']],timeout=60)
        else:
            for file in stage['files']:atomic_copy(payload/name/file,old/file)
        wait_ready(stage['unit'])
        compare(before,snapshot(),stage['unit'] if stage['restart'] else None)
        if financial!=data_digest():raise ValueError('canonical_data_changed')
        installed=unit(stage['unit'])
        if installed['User']!=row['User'] or installed['WorkingDirectory']!=str(clone if stage['restart'] else old):raise ValueError('service_identity_changed')
        deployed=clone if stage['restart'] else old
        for file,entry in stage['files'].items():
            if sha(deployed/file)!=entry['after']:raise ValueError('installed_file_mismatch')
        save(folder/'accepted.json',{'ready':True,'only_visual_files':True,'canonical_data_unchanged':True,
          'protected_services_unchanged':True,'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'new':str(deployed)})
        print(name+'=accepted restart='+str(stage['restart']).lower(),flush=True)
    except BaseException:
        if changed:rollback(name,manifest)
        raise

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['preflight','apply','rollback']);parser.add_argument('stage',choices=STAGE_NAMES);parser.add_argument('payload',type=Path);args=parser.parse_args()
    try:
        if os.geteuid()!=0:raise ValueError('root_required')
        os.umask(0o077)
        manifest=json.loads((args.payload/'manifest.json').read_text())
        if manifest['approved']!='ca0d10ece2f3026a69c08c36f5759ed3fa0d1220':raise ValueError('source_revision_mismatch')
        if args.mode=='preflight':
            validate_files(manifest['stages'][args.stage],args.payload/args.stage);test_bot_identity(manifest['stages'][args.stage]);snapshot();data_digest();config_for_age();wait_ready(manifest['stages'][args.stage]['unit']);print(args.stage+'=preflight_passed')
        else:
            with open('/run/kudavpn-branding.lock','w') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                if args.mode=='rollback':rollback(args.stage,manifest)
                else:apply(args.stage,manifest,args.payload)
    except BaseException as error:
        # Codes only; no subprocess output, DSNs, tokens or private URL paths.
        import re
        code=str(error) if isinstance(error,ValueError) and re.fullmatch('[a-z_]{1,80}',str(error)) else type(error).__name__
        print('branding_stopped='+code,flush=True)
        raise SystemExit(1) from None
