"""Owner runs locally with hidden passphrase. Never sends recovery key to VPS.

Only ciphertext is persisted on Mac. Plaintext flows into an isolated tmpfs
PostgreSQL lab. Public result contains counts/status, never rows or keys.
"""
import datetime as dt
import getpass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import secrets
import shutil
import subprocess as sp
import sys
import tarfile
import warnings

NAME=re.compile(r'\d{8}T\d{6}Z-[a-f0-9]{8}-(digitalocean|justhost)-(live|pilot|website|roles|config|run)(\.dump|\.manifest\.json|\.sql|\.tar|\.json)\.age')
REMOTE='/private/tmp/kuda-monitor-do-ssh.sh'
ROOT=Path.home()/'Documents/KudaVPN-private/backups'


def remote(action,name,label=None,data=None):
    if action not in ['start','load','verify','finish','abort'] or not re.fullmatch(r'kudavpn-restorecheck-[a-f0-9]{12}',name):raise ValueError('invalid_lab_request')
    if label is not None and label not in ['live','pilot','website']:raise ValueError('invalid_database_label')
    cmd='python3 /usr/local/lib/kudavpn-backup/restore_lab.py '+action+' '+name+(' '+label if label else '')
    if action=='load':return sp.Popen([REMOTE,cmd],stdin=sp.PIPE,stdout=sp.PIPE,stderr=sp.PIPE)
    p=sp.run([REMOTE,cmd],input=data,stdout=sp.PIPE,stderr=sp.PIPE,timeout=180)
    if p.returncode:
        try:code=json.loads(p.stdout).get('code','operation_failed')
        except Exception:code='operation_failed'
        if not isinstance(code,str) or not re.fullmatch('[A-Za-z_]{1,80}',code):code='operation_failed'
        raise ValueError('lab_'+action+'_'+code)
    return json.loads(p.stdout)


def age_process(binary,identity,path):
    read,write=os.pipe()
    try:
        os.write(write,identity);os.close(write);write=None
        return sp.Popen([binary,'-d','-i','/dev/fd/'+str(read),str(path)],pass_fds=(read,),stdout=sp.PIPE,stderr=sp.PIPE)
    finally:
        os.close(read)
        if write is not None:os.close(write)


def plain(binary,identity,path,maximum=4*1024**2):
    p=age_process(binary,identity,path)
    try:
        result=p.stdout.read(maximum+1)
        if len(result)>maximum:raise ValueError('metadata_size_limit')
        _,err=p.communicate(timeout=90)
        if p.returncode:raise ValueError('archive_authentication_failed')
        return result
    finally:
        if p.poll() is None:p.kill();p.wait()


def stream_load(binary,identity,path,name,label):
    decrypt=age_process(binary,identity,path);target=remote('load',name,label)
    try:
        shutil.copyfileobj(decrypt.stdout,target.stdin,65536)
        decrypt.wait(timeout=90)
        if decrypt.returncode:raise ValueError('archive_authentication_failed')
        target.stdin.close();target.stdin=None
        raw,_=target.communicate(timeout=180)
        if target.returncode or json.loads(raw).get('restored') is not True:raise ValueError('isolated_restore_failed')
    finally:
        for p in [decrypt,target]:
            if p.poll() is None:p.kill();p.wait()


def check_config(binary,identity,path,host):
    p=age_process(binary,identity,path);names=set()
    try:
        with tarfile.open(fileobj=p.stdout,mode='r|') as tar:
            for item in tar:
                name=PurePosixPath(item.name)
                if name.is_absolute() or '..' in name.parts or not item.isfile() or item.size>64*1024**2:raise ValueError('unsafe_configuration_archive')
                names.add(item.name)
        # Drain trailing tar padding to authenticate the ENTIRE age stream.
        for chunk in iter(lambda:p.stdout.read(65536),b''):pass
        p.wait(timeout=90)
        if p.returncode:raise ValueError('configuration_authentication_failed')
    finally:
        if p.poll() is None:p.kill();p.wait()
    required=({'opt/kudavpn-xray/config/config.json','opt/kudavpn/releases/ba2f0f6a3daf5400/.env.production'} if host=='digitalocean'
              else {'etc/xray/config.json','etc/xray/alt-8443.json','etc/kudavpn-site-payments/payments.json','etc/kudavpn-website-review/oidc.json','etc/kudavpn-website-review/tls.key','opt/kudavpn-telegram-pilot/private-welcome/onboarding.sqlite'})
    if not required<=names:raise ValueError('required_configuration_missing')
    return {'authenticated':True,'required_files_present':True,'files':len(names)}


def run():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0));warnings.simplefilter('error',getpass.GetPassWarning)
    if len(sys.argv)!=1 or not sys.stdin.isatty():raise ValueError('interactive_terminal_required')
    spec=importlib.util.spec_from_file_location('backup_key',ROOT/'prepare-recovery-key.py')
    key=importlib.util.module_from_spec(spec);spec.loader.exec_module(key)
    key.private_directory(ROOT)
    key.private_directory(ROOT/'copies')
    index=json.loads(key.read_private(ROOT/'copies/index.json'))
    if set(index)!=set(['digitalocean','justhost']):raise ValueError('incomplete_copy_index')
    artifacts={}
    for host,status in index.items():
        if status.get('state')!='ok' or status.get('offsite') is not True:raise ValueError('incomplete_backup')
        for item in status['artifacts']:
            name=item['name'];match=NAME.fullmatch(name)
            if not match or match.group(1)!=host:raise ValueError('invalid_archive_name')
            path=ROOT/'copies'/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size!=item['bytes']:raise ValueError('archive_size_or_path')
            h=hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(65536),b''):h.update(chunk)
            if h.hexdigest()!=item['sha256']:raise ValueError('ciphertext_checksum')
            artifacts[(host,match.group(2),match.group(3))]=path
    binary=key.tool(ROOT/'tools','age');keygen=key.tool(ROOT/'tools','age-keygen')
    identity=key.unwrap(key.read_private(ROOT/'recovery-key.encrypted.json'),getpass.getpass('Unlock recovery key locally (hidden): '))
    if key.recipient(keygen,identity)!=(ROOT/'recipient.txt').read_text().strip():raise ValueError('recovery_recipient_mismatch')
    name='kudavpn-restorecheck-'+secrets.token_hex(6);finished=False
    result={'started_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'container':name,'runs':{host:x['run'] for host,x in index.items()},'configurations':{},'passed':False}
    try:
        result['stage']='start';result['isolation']=remote('start',name)
        for host,label in [('digitalocean','live'),('justhost','pilot'),('justhost','website')]:
            result['stage']='load_'+label
            stream_load(binary,identity,artifacts[(host,label,'.dump')],name,label)
            result['stage']='verify_'+label
            manifest=plain(binary,identity,artifacts[(host,label,'.manifest.json')])
            checked=remote('verify',name,label,data=manifest)
            if checked.get('passed') is not True:raise ValueError('restored_records_mismatch')
            print('PASS isolated database:',label,flush=True)
        for host in ['digitalocean','justhost']:
            result['stage']='configuration_'+host
            result['configurations'][host]=check_config(binary,identity,artifacts[(host,'config','.tar')],host)
            roles=plain(binary,identity,artifacts[(host,'roles','.sql')])
            if b'CREATE ROLE' not in roles:raise ValueError('roles_backup_missing')
            summary=json.loads(plain(binary,identity,artifacts[(host,'run','.json')]))
            if summary.get('run')!=index[host]['run'] or summary.get('host')!=host:raise ValueError('run_manifest_mismatch')
        result['stage']='finish';result['lab']=remote('finish',name);finished=True;result['passed']=True;result['stage']='completed'
    except Exception as exc:
        code=str(exc)
        result['error_code']=code if re.fullmatch('[A-Za-z_]{1,100}',code) else type(exc).__name__
        raise
    finally:
        if not finished:
            try:result['cleanup']=remote('abort',name)
            except Exception:result['cleanup']={'state':'needs_operator_check','container':name}
        del identity
        result['completed_utc']=dt.datetime.now(dt.timezone.utc).isoformat()
        path=ROOT/'restore-result.json'
        if path.exists():
            previous=path.read_bytes()
            archived=ROOT/('restore-result-'+hashlib.sha256(previous).hexdigest()[:12]+'.json')
            if not archived.exists():
                fd=os.open(archived,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as f:f.write(previous);f.flush();os.fsync(f.fileno())
        tmp=ROOT/('.restore-result-'+secrets.token_hex(8))
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:json.dump(result,f,sort_keys=True);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    print('Restore verification PASSED. Disposable lab removed; working services unchanged.')
    print('Safe result file:',ROOT/'restore-result.json')


if __name__=='__main__':
    try:run()
    except Exception as exc:
        print('Restore verification NOT completed. No password or key was logged.')
        code=str(exc)
        print('Safe error code:',code if re.fullmatch('[A-Za-z_]{1,100}',code) else type(exc).__name__)
        print('Report the failure message only; never send keys or passphrases.')
        raise SystemExit(1)
