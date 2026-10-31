"""Encrypted backups; no application/DB mutations, no archive pruning.

Receiver is unprivileged. A separate root sealer creates immutable-to-uploader
vault inodes. Only age ciphertext and public checksums ever touch normal disk.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import signal
import sqlite3
import stat
import subprocess as sp
import sys
import tarfile
import time

BASE = Path('/var/lib/kudavpn-backup')
CONFIG = Path('/etc/kudavpn-backup/config.json')
MAX_FILE = 256 * 1024**2
QUOTA = 2 * 1024**3
NAME = re.compile(r'\d{8}T\d{6}Z-[a-f0-9]{8}-(digitalocean|justhost)-(live|pilot|website|roles|config|run)(\.dump|\.manifest\.json|\.sql|\.tar|\.json)\.age')


class Failure(Exception):
    """Codes only: never expose subprocess stderr, DSNs or exception messages."""


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(65536), b''): h.update(data)
    return h.hexdigest()


def atomic(path, data, mode=0o600):
    path = Path(path)
    tmp = path.parent / ('.new-' + secrets.token_hex(8))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), mode); f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        tmp.unlink(missing_ok=True)


def read_json(path, maximum=65536):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > maximum:
            raise Failure('unsafe_metadata')
        return json.loads(f.read(maximum + 1))


@contextlib.contextmanager
def lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode): raise Failure('unsafe_lock')
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise Failure('already_running') from None
        yield
    finally: os.close(fd)


def space(path):
    d = shutil.disk_usage(path)
    if d.free < max(2 * 1024**3, d.total // 5): raise Failure('disk_low')
    return d.free


def header(value, peer):
    if (not isinstance(value, dict) or set(value) != {'name','bytes','sha256'}
            or not isinstance(value['name'], str) or not NAME.fullmatch(value['name'])
            or NAME.fullmatch(value['name']).group(1) != peer
            or type(value['bytes']) is not int or not 32 <= value['bytes'] <= MAX_FILE
            or not isinstance(value['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['sha256'])):
        raise Failure('invalid_header')
    return value


def peer_paths(peer):
    if peer not in ('digitalocean','justhost'): raise Failure('invalid_peer')
    return BASE/'inbox'/peer, BASE/'vault'/peer, BASE/'receipts'/peer


def quota(inbox, vault, reserve=0):
    total = sum(p.stat().st_size for root in (inbox, vault) for p in root.iterdir() if p.is_file())
    if total + reserve > QUOTA: raise Failure('quota')


def receive(peer, source=None, output=None):
    source = source or sys.stdin.buffer; output = output or sys.stdout
    signal.alarm(180)
    inbox, vault, receipts = peer_paths(peer)
    raw = source.readline(2049)
    if len(raw) > 2048 or not raw.endswith(b'\n'): raise Failure('invalid_header')
    value = json.loads(raw)
    if value == {'op':'probe'}:
        space(BASE); print(json.dumps({'peer':peer,'protocol':1}), file=output); return
    if value == {'op':'status'}:
        value=read_json(BASE/'status.json')
        value['integrity']=read_json(BASE/'integrity.json') if (BASE/'integrity.json').exists() else {}
        print(json.dumps(value), file=output); return
    value = header(value, peer)
    with lock(inbox/'.receive.lock'):
        space(BASE); quota(inbox,vault,reserve=2*value['bytes'])
        receipt = receipts/(value['name']+'.json')
        if receipt.exists():
            old = read_json(receipt)
            if any(old.get(k)!=value[k] for k in value): raise Failure('collision')
            # Drain retry input too; otherwise a large retry hits BrokenPipe.
            h=hashlib.sha256();left=value['bytes']
            while left:
                data=source.read(min(65536,left))
                if not data: raise Failure('truncated_upload')
                h.update(data);left-=len(data)
            if source.read(1) or h.hexdigest()!=value['sha256']: raise Failure('upload_checksum')
            print(json.dumps(old),file=output); return
        path = inbox/(value['name']+'.upload')
        fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        h = hashlib.sha256(); left=value['bytes']; first=True
        try:
            with os.fdopen(fd,'wb') as f:
                while left:
                    data=source.read(min(65536,left))
                    if not data: raise Failure('truncated_upload')
                    if first and not data.startswith(b'age-encryption.org/v1\n'): raise Failure('not_age')
                    first=False; h.update(data); f.write(data); left-=len(data)
                if source.read(1): raise Failure('oversized_upload')
                f.flush(); os.fsync(f.fileno())
            if h.hexdigest()!=value['sha256']: raise Failure('upload_checksum')
            atomic(inbox/(value['name']+'.ready.json'),json.dumps(value).encode())
            deadline=time.monotonic()+100
            while time.monotonic()<deadline:
                if receipt.exists():
                    ack=read_json(receipt)
                    if all(ack.get(k)==value[k] for k in value):
                        print(json.dumps(ack),file=output); return
                    raise Failure('receipt_mismatch')
                time.sleep(0.25)
            raise Failure('seal_timeout')
        except BaseException:
            # Only this newly created staging file; NEVER a sealed/old backup.
            if not (inbox/(value['name']+'.ready.json')).exists(): path.unlink(missing_ok=True)
            raise


def seal_one(meta, peer):
    inbox,vault,receipts=peer_paths(peer)
    value=header(read_json(meta),peer); src=inbox/(value['name']+'.upload')
    final=vault/value['name']; ack=receipts/(value['name']+'.json')
    if final.exists():
        if final.is_symlink() or digest(final)!=value['sha256']: raise Failure('collision')
    else:
        space(BASE); quota(inbox,vault)
        fd=os.open(src,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        tmp=vault/('.seal-'+secrets.token_hex(8))
        try:
            with os.fdopen(fd,'rb') as source:
                s=os.fstat(source.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size!=value['bytes']: raise Failure('unsafe_upload')
                h=hashlib.sha256(); count=0
                with tmp.open('xb') as dest:
                    os.fchmod(dest.fileno(),0o440)
                    while data:=source.read(65536):
                        if count==0 and not data.startswith(b'age-encryption.org/v1\n'): raise Failure('not_age')
                        count+=len(data)
                        if count>MAX_FILE: raise Failure('oversized_upload')
                        h.update(data); dest.write(data)
                    dest.flush(); os.fsync(dest.fileno())
                if count!=value['bytes'] or h.hexdigest()!=value['sha256']: raise Failure('seal_checksum')
                os.chown(tmp,0,vault.stat().st_gid)
                os.link(tmp,final)  # Exclusive publication; never replaces a previous inode.
        finally: tmp.unlink(missing_ok=True)
    receipt={**value,'peer':peer,'sealed_utc':utc(),'verified':True}
    directory=os.open(vault,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
    if ack.exists():
        previous=read_json(ack)
        if any(previous.get(k)!=value[k] for k in value):raise Failure('receipt_collision')
        receipt=previous
    else:
        atomic(ack,json.dumps(receipt).encode(),0o440)
        os.chown(ack,0,receipts.stat().st_gid)
    # Newly submitted transit duplicates only; sealed archives remain forever.
    src.unlink(missing_ok=True); meta.unlink(missing_ok=True)
    return receipt


def seal():
    with lock(BASE/'.seal.lock'):
        for peer in ('digitalocean','justhost'):
            inbox,_,_=peer_paths(peer)
            if not inbox.exists(): continue
            for meta in sorted(inbox.glob('*.ready.json')):
                try: seal_one(meta,peer)
                except Exception: print('backup_seal_failed',flush=True)


def checked(args, data=None, timeout=60):
    r=sp.run(args,input=data,stdout=sp.PIPE,stderr=sp.PIPE,timeout=timeout)
    if r.returncode: raise Failure('subprocess_failed')
    return r.stdout


META_SQL="""SELECT jsonb_build_object(
'columns',(SELECT jsonb_agg(to_jsonb(x) ORDER BY table_name,ordinal_position) FROM (SELECT table_name,column_name,ordinal_position,column_default,is_nullable,data_type,udt_name FROM information_schema.columns WHERE table_schema='public') x),
'constraints',(SELECT jsonb_agg(to_jsonb(x) ORDER BY relname,conname) FROM (SELECT r.relname,c.conname,pg_get_constraintdef(c.oid) definition,c.convalidated FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public') x),
'indexes',(SELECT jsonb_agg(to_jsonb(x) ORDER BY tablename,indexname) FROM (SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public') x))::text"""


class Snapshot:
    def __init__(self,cmd):
        self.proc=sp.Popen(cmd,stdin=sp.PIPE,stdout=sp.PIPE,stderr=sp.PIPE)
        self.proc.stdin.write(b'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n'); self.proc.stdin.flush()
        self.snapshot=self.query('SELECT pg_export_snapshot()')
        if not re.fullmatch('[0-9A-Fa-f-]+',self.snapshot): raise Failure('snapshot')

    def query(self,sql):
        # Each query returns exactly one line, no statement/row logging.
        sql=sql.strip().rstrip(';')
        self.proc.stdin.write(("SELECT 'KUDA:' || ("+sql+")::text;\n").encode()); self.proc.stdin.flush()
        deadline=time.monotonic()+65
        while time.monotonic()<deadline:
            if not select.select([self.proc.stdout],[],[],max(.01,deadline-time.monotonic()))[0]: continue
            row=self.proc.stdout.readline(8*1024**2)
            if not row: raise Failure('snapshot_query')
            if row.startswith(b'KUDA:'): return row[5:].decode().strip()
        raise Failure('snapshot_timeout')

    def manifest(self):
        tables=json.loads(self.query("SELECT coalesce(json_agg(tablename ORDER BY tablename),'[]')::text FROM pg_tables WHERE schemaname='public'"))
        rows={}
        for table in tables:
            ident='"'+table.replace('"','""')+'"'
            rows[table]=json.loads(self.query("SELECT json_build_object('count',count(*),'hash',md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text COLLATE \"C\")::text,'[]')))::text FROM public."+ident+' t'))
        return {'database':self.query('SELECT current_database()'),'revision':self.query('SELECT version_num FROM alembic_version'),
                'tables':rows,'metadata':json.loads(self.query(META_SQL)),'utc':utc(),
                'postgresql':json.loads(self.query("SELECT jsonb_object_agg(name,setting)::text FROM pg_settings WHERE name IN ('server_version','server_encoding','lc_collate','lc_ctype','timezone','port','config_file','hba_file','ident_file','data_directory')"))}

    def close(self):
        if self.proc.poll() is None:
            try: self.proc.communicate(b'ROLLBACK;\n',timeout=8)
            except (sp.TimeoutExpired,BrokenPipeError): self.proc.kill(); self.proc.wait()


class Source:
    def __init__(self,config):
        self.host=config['host']; self.config=config
        if self.host=='digitalocean':
            info=json.loads(checked(['docker','inspect','kudavpn-premium-bot-1']))[0]
            env=dict(x.split('=',1) for x in info['Config']['Env'] if '=' in x)
            from urllib.parse import urlsplit
            url=urlsplit(env['DATABASE_URL'].replace('postgresql+asyncpg:','postgresql:'))
            if url.port!=5433 or url.path.lstrip('/')!='kudavpn_live_20261010152329': raise Failure('wrong_live_database')
            self.databases={'live':url.path.lstrip('/')}
        elif self.host=='justhost': self.databases={'pilot':'kudavpn_telegram_pilot','website':'kudavpn_website_test'}
        else: raise Failure('wrong_host')

    def command(self,tool,db=None,args=()):
        if self.host=='digitalocean':
            shell='export PGPASSWORD="$POSTGRES_PASSWORD" PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=60000 -c timezone=UTC"; exec "$@" -h 127.0.0.1 -p 5433 -U "$POSTGRES_USER"'
            cmd=['docker','exec','-i','kudavpn-premium-postgres-1','sh','-c',shell,'backup',tool]
        else:
            cmd=['runuser','-u','postgres','--','env','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=60000 -c timezone=UTC',tool]
        if db: cmd+=['-d',db]
        return cmd+list(args)

    def wait_ready(self):
        deadline=time.monotonic()+60
        db=next(iter(self.databases.values()))
        while time.monotonic()<deadline:
            result=sp.run(self.command('pg_isready',db,['-t','3']),stdout=sp.DEVNULL,stderr=sp.DEVNULL,timeout=8)
            if result.returncode==0:return
            time.sleep(2)
        raise Failure('database_not_ready')


def encrypt(config,path,writer):
    if path.exists(): raise Failure('collision')
    age=config['age']
    if digest(age)!=config['age_sha256']: raise Failure('age_binary_changed')
    partial=path.with_name(path.name+'.partial')
    fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as output:
            proc=sp.Popen([age,'-r',config['recipient']],stdin=sp.PIPE,stdout=output,stderr=sp.PIPE)
            try:
                writer(proc.stdin); proc.stdin.close(); proc.stdin=None
                _,err=proc.communicate(timeout=120)
                if proc.returncode: raise Failure('encryption_failed')
            except BaseException:
                if proc.poll() is None: proc.kill();proc.wait()
                raise
            output.flush();os.fsync(output.fileno())
        if not 32<=partial.stat().st_size<=MAX_FILE: raise Failure('archive_size')
        os.link(partial,path)
    finally: partial.unlink(missing_ok=True)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
    return {'name':path.name,'bytes':path.stat().st_size,'sha256':digest(path)}


def copy_command(cmd,target,timeout=120):
    proc=sp.Popen(cmd,stdout=target,stderr=sp.PIPE)
    try:
        _,err=proc.communicate(timeout=timeout)
        if proc.returncode: raise Failure('dump_failed')
    finally:
        if proc.poll() is None: proc.kill();proc.wait()


def configs(config,stream):
    skip={'.git','__pycache__','backups','venv','.venv','node_modules','.pytest_cache','heartbeat.json'}
    with tarfile.open(fileobj=stream,mode='w|') as tar:
        seen=set()
        for item in config['capture']:
            path=Path(item)
            if not path.exists(): raise Failure('missing_config_path')
            paths=[path] if not path.is_dir() else [p for p in path.rglob('*') if not any(x in skip for x in p.relative_to(path).parts)]
            for p in paths:
                if p.is_symlink() or not p.is_file() or str(p) in seen or p.name.endswith(('-wal','-shm','-journal')): continue
                seen.add(str(p)); before=p.stat()
                if before.st_size>64*1024**2: raise Failure('config_file_too_large')
                info=tarfile.TarInfo(str(p).lstrip('/'));info.mode=stat.S_IMODE(before.st_mode);info.uid=before.st_uid;info.gid=before.st_gid;info.mtime=int(before.st_mtime)
                if p.suffix in ('.sqlite','.db'):
                    source=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True,timeout=5)
                    dest=sqlite3.connect(':memory:')
                    try: source.backup(dest);data=dest.serialize()
                    finally: dest.close();source.close()
                    info.size=len(data);tar.addfile(info,io.BytesIO(data))
                else:
                    info.size=before.st_size
                    with p.open('rb') as f: tar.addfile(info,f)
                    after=p.stat()
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise Failure('config_changed_during_copy')
        if config['host']=='digitalocean':
            for name in ['kudavpn-premium-bot-1','kudavpn-premium-postgres-1','kudavpn-xray-xray-1']:
                data=checked(['docker','inspect',name]);info=tarfile.TarInfo('runtime/'+name+'.json');info.size=len(data);info.mode=0o600;tar.addfile(info,io.BytesIO(data))
            # Configuration ONLY, never a live filesystem copy of PGDATA.
            for name in ['postgresql.conf','postgresql.auto.conf','pg_hba.conf','pg_ident.conf','PG_VERSION']:
                data=checked(['docker','exec','kudavpn-premium-postgres-1','cat','/var/lib/postgresql/data/'+name])
                info=tarfile.TarInfo('runtime/postgresql/'+name);info.size=len(data);info.mode=0o600;tar.addfile(info,io.BytesIO(data))
        data=checked(['systemctl','show',*config['units'],'--property=Id,ActiveState,SubState,UnitFileState,WorkingDirectory,MainPID,ActiveEnterTimestamp'])
        info=tarfile.TarInfo('runtime/systemd-state.txt');info.size=len(data);info.mode=0o600;tar.addfile(info,io.BytesIO(data))


def ssh(config):
    credential=Path(os.environ['CREDENTIALS_DIRECTORY'])/'upload-key'
    if not credential.is_file(): raise Failure('upload_credential_missing')
    return ['ssh','-T','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/etc/kudavpn-backup/known_hosts',
            '-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-o','ServerAliveInterval=10','-o','ServerAliveCountMax=3',
            '-i',str(credential),config['remote_user']+'@'+config['remote_ip']]


def upload(config,path,meta):
    wire={k:meta[k] for k in ['name','bytes','sha256']}
    for attempt in range(3):
        proc=sp.Popen(ssh(config),stdin=sp.PIPE,stdout=sp.PIPE,stderr=sp.PIPE)
        try:
            proc.stdin.write(json.dumps(wire).encode()+b'\n')
            with path.open('rb') as f: shutil.copyfileobj(f,proc.stdin,65536)
            proc.stdin.close();proc.stdin=None
            raw,_=proc.communicate(timeout=130)
            if proc.returncode: raise Failure('offsite_transfer')
            ack=json.loads(raw)
            if ack.get('verified') is not True or any(ack.get(k)!=wire[k] for k in wire): raise Failure('offsite_receipt')
            return ack
        except (Failure,OSError,ValueError,sp.SubprocessError):
            if proc.poll() is None: proc.kill();proc.wait()
            if attempt==2: raise Failure('offsite_transfer') from None
            time.sleep(2)


def run_backup(config):
    with lock(BASE/'.backup.lock'):
        status_path=BASE/'status.json'
        previous=read_json(status_path) if status_path.exists() else {}
        status={**previous,'host':config['host'],'last_attempt':utc(),'state':'running','error':None}
        atomic(status_path,json.dumps(status).encode(),0o644)
        phase='preflight'
        try:
            free=space(BASE)
            if sum(p.stat().st_size for p in (BASE/'local').glob('*.age'))>QUOTA: raise Failure('quota')
            source=Source(config);source.wait_ready();run=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(4)
            metas=[]; manifests={}
            def artifact(suffix,writer):
                path=BASE/'local'/(run+'-'+config['host']+'-'+suffix+'.age')
                meta=encrypt(config,path,writer);metas.append(meta)
            phase='database'
            for tag,db in source.databases.items():
                snap=Snapshot(source.command('psql',db,['-X','-q','-A','-t','-v','ON_ERROR_STOP=1']))
                try:
                    manifest=snap.manifest()
                    expected=config['revisions'][tag]
                    if manifest['revision']!=expected: raise Failure('schema_changed_review_required')
                    artifact(tag+'.dump',lambda out:copy_command(source.command('pg_dump',db,['--format=custom','--lock-wait-timeout=5000','--snapshot='+snap.snapshot]),out))
                    artifact(tag+'.manifest.json',lambda out:out.write(json.dumps(manifest,sort_keys=True).encode()))
                    manifests[tag]={'database':db,'revision':manifest['revision'],'tables':len(manifest['tables'])}
                finally: snap.close()
            phase='roles'
            artifact('roles.sql',lambda out:copy_command(source.command('pg_dumpall',args=['--globals-only']),out))
            phase='configuration'
            artifact('config.tar',lambda out:configs(config,out))
            phase='manifest'
            summary={'run':run,'host':config['host'],'utc':utc(),'databases':manifests,'artifacts':metas,'capture':config['capture']}
            artifact('run.json',lambda out:out.write(json.dumps(summary,sort_keys=True).encode()))
            phase='offsite'
            status.update(run=run,artifacts=metas,databases=manifests)
            atomic(status_path,json.dumps(status).encode(),0o644)
            for meta in metas:
                meta['sealed_utc']=upload(config,BASE/'local'/meta['name'],meta)['sealed_utc']
            status={'host':config['host'],'last_attempt':status['last_attempt'],'last_success':status['last_attempt'],'completed_utc':utc(),'state':'ok','error':None,
                    'run':run,'artifacts':metas,'databases':manifests,'encryption':True,'offsite':True,'ciphertext_checksums':True,'free_bytes':free}
            atomic(status_path,json.dumps(status).encode(),0o644)
            print(json.dumps({'host':config['host'],'state':'ok','run':run,'artifacts':len(metas),'bytes':sum(m['bytes'] for m in metas)}))
        except Exception as exc:
            code=str(exc) if isinstance(exc,Failure) and re.fullmatch('[a-z_]+',str(exc)) else 'operation_failed'
            status.update(state='error',error=phase+':'+code)
            atomic(status_path,json.dumps(status).encode(),0o644)
            print(json.dumps({'state':'error','stage':phase,'code':code}));raise Failure(code) from None


def retry(config):
    with lock(BASE/'.backup.lock'):
        path=BASE/'status.json'
        if not path.exists():print('no_pending_transfer');return
        status=read_json(path)
        if status.get('state')!='error' or not str(status.get('error','')).startswith('offsite:') or not status.get('artifacts'):
            print('no_pending_transfer');return
        try:
            space(BASE)
            for item in status['artifacts']:
                header({k:item[k] for k in ['name','bytes','sha256']},config['host'])
                artifact=BASE/'local'/item['name']
                if artifact.stat().st_size!=item['bytes'] or digest(artifact)!=item['sha256']:raise Failure('local_checksum')
                item['sealed_utc']=upload(config,artifact,item)['sealed_utc']
            status.update(state='ok',error=None,last_success=status['last_attempt'],completed_utc=utc(),encryption=True,offsite=True,ciphertext_checksums=True)
            atomic(path,json.dumps(status).encode(),0o644);print('pending_transfer_completed')
        except Exception:
            atomic(path,json.dumps(status).encode(),0o644);raise Failure('pending_transfer_failed') from None


def health_errors(status,now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    errors=[]
    try:
        stamp=dt.datetime.fromisoformat(status['last_success'])
        if not -60<=(now-stamp).total_seconds()<=26*3600: errors.append('stale')
    except (KeyError,ValueError,TypeError): errors.append('no_data')
    if status.get('state')=='running':
        try:
            elapsed=(now-dt.datetime.fromisoformat(status['last_attempt'])).total_seconds()
            if not -60<=elapsed<=21*60:errors.append('job_stuck')
        except (KeyError,ValueError,TypeError):errors.append('job_stuck')
    elif status.get('state')!='ok': errors.append('job_error')
    for key in ('encryption','offsite','ciphertext_checksums'):
        if status.get(key) is not True: errors.append(key)
    return errors


def verify(config):
    result={'host':config['host'],'checked_utc':utc(),'state':'ok','files':0}
    try:
        result['free_bytes']=space(BASE)
        if digest(config['age'])!=config['age_sha256']: raise Failure('age_binary_changed')
        status=read_json(BASE/'status.json')
        for item in status.get('artifacts',[]):
            header({k:item[k] for k in ['name','bytes','sha256']},config['host'])
            p=BASE/'local'/item['name']
            if not p.is_file() or p.stat().st_size!=item['bytes'] or digest(p)!=item['sha256']: raise Failure('local_checksum')
            result['files']+=1
        for peer in ('digitalocean','justhost'):
            _,vault,receipts=peer_paths(peer)
            if not receipts.exists(): continue
            all_receipts=sorted(receipts.glob('*.json'))
            if not all_receipts: continue
            latest=all_receipts[-1].name[:25]
            for receipt in all_receipts:
                if not receipt.name.startswith(latest): continue
                item=header({k:read_json(receipt)[k] for k in ['name','bytes','sha256']},peer)
                p=vault/item['name']
                if p.stat().st_size!=item['bytes'] or digest(p)!=item['sha256']: raise Failure('vault_checksum')
                result['files']+=1
        if result['files']==0: raise Failure('no_data')
    except Exception as exc:
        result.update(state='error',error=str(exc) if isinstance(exc,Failure) else 'verification_failed')
    atomic(BASE/'integrity.json',json.dumps(result).encode(),0o644)
    print(json.dumps(result))
    return result


def health(config):
    # Independent observer: does not edit/restart Monitor or use getUpdates.
    import urllib.request
    with lock(BASE/'.health.lock'):
        results={};statuses={}
        try:
            statuses[config['host']]=read_json(BASE/'status.json')
            statuses[config['host']]['integrity']=read_json(BASE/'integrity.json')
            statuses[config['remote_host']]=json.loads(checked(ssh(config),b'{"op":"status"}\n',timeout=30))
        except Exception: results['peer']=['no_data']
        for host in ('digitalocean','justhost'):
            results[host]=health_errors(statuses.get(host,{}))
            integrity=statuses.get(host,{}).get('integrity',{})
            try:
                age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(integrity['checked_utc'])).total_seconds()
                if integrity.get('state')!='ok' or not -60<=age<=1800: results[host].append('integrity')
            except Exception: results[host].append('integrity_no_data')
        try: space(BASE)
        except Failure: results['disk']=['disk_low']
        for m in statuses.get(config['host'],{}).get('artifacts',[]):
            path=BASE/'local'/m['name']
            if not path.is_file() or digest(path)!=m['sha256']: results['local_archive']=['checksum']
        failures={k:v for k,v in results.items() if v}
        state_path=BASE/'health-state.json'
        before=read_json(state_path) if state_path.exists() else {}
        fingerprint=json.dumps(failures,sort_keys=True)
        state={'checked_utc':utc(),'errors':failures,'fingerprint':fingerprint,'last_notice':before.get('last_notice',0),'delivery':before.get('delivery')}
        notice=bool(failures) and (before.get('fingerprint')!=fingerprint or time.time()-before.get('last_notice',0)>4*3600)
        recovered=not failures and bool(before.get('errors'))
        retry_notice=before.get('delivery')=='unconfirmed' and time.time()-before.get('last_notice',0)>4*3600
        if notice or recovered or retry_notice:
            state['last_notice']=time.time();state['delivery']='pending'
            atomic(state_path,json.dumps(state).encode())
            try:
                folder=Path('/var/lib/kudavpn-monitor')
                identity=read_json(folder/'identity.json');owner=identity['owner_id']
                if type(owner) is not int or owner<=0 or not(identity.get('owner_start_verified') or identity.get('owner_contact_verified')): raise Failure('owner_not_verified')
                token=(folder/'bot-token').read_text().strip()
                message='KudaVPN Backup: '+('ERROR '+fingerprint if failures else 'RECOVERED: encrypted/offsite backups current')
                data=json.dumps({'chat_id':owner,'text':message,'protect_content':True}).encode()
                req=urllib.request.Request('https://api.telegram.org/bot'+token+'/sendMessage',data=data,headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req,timeout=15) as response: reply=json.load(response)
                if reply.get('ok') is not True or reply['result']['chat']['id']!=owner: raise Failure('delivery_not_verified')
                state['delivery']='confirmed';state['message_id']=reply['result']['message_id']
            except Exception: state['delivery']='unconfirmed'
        atomic(state_path,json.dumps(state).encode())
        print(json.dumps({'backup_health':'error' if failures else 'ok','checked_utc':state['checked_utc'],'delivery':state.get('delivery')}))
        return not failures and state.get('delivery')!='unconfirmed'


def main():
    os.umask(0o077)
    if len(sys.argv)<2: return 2
    try:
        if sys.argv[1]=='receive' and len(sys.argv)==3: receive(sys.argv[2]);return 0
        if os.geteuid()!=0: raise Failure('root_required')
        if sys.argv[1]=='seal': seal();return 0
        config=read_json(CONFIG)
        if sys.argv[1]=='backup': run_backup(config)
        elif sys.argv[1]=='retry': retry(config)
        elif sys.argv[1]=='verify': return 0 if verify(config)['state']=='ok' else 1
        elif sys.argv[1]=='health': return 0 if health(config) else 1
        elif sys.argv[1]=='probe': print(checked(ssh(config),b'{"op":"probe"}\n',30).decode().strip())
        else: raise Failure('invalid_command')
        return 0
    except BaseException as exc:
        print('backup_operation_failed',file=sys.stderr)
        return 1


if __name__=='__main__': raise SystemExit(main())
