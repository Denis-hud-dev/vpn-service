"""Snapshot-consistent read-only dump and isolated restore verification.

Run from repository root with DATABASE_URL and PostgreSQL env already loaded.
No polling and no working database writes. Backup contains private user data.
"""
import datetime,hashlib,json,os,secrets,subprocess,time,shutil
from pathlib import Path
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
D=shutil.which('docker')
if not D: raise SystemExit('Docker CLI is required')
root=Path.cwd();container='kudavpn-restore-check-'+secrets.token_hex(4)
backup_dir=root/'backups';backup_dir.mkdir(mode=0o700,exist_ok=True)
path=backup_dir/('kudavpn-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.dump')
assert not path.exists()
def run(args,**kwargs):
    return subprocess.run([D,*args],check=True,stderr=subprocess.PIPE,**kwargs)
def services():
    result={}
    for name in ('bot','postgres'):
        cid=run(['compose','ps','-a','-q',name],stdout=subprocess.PIPE,text=True).stdout.strip()
        state=run(['inspect','--format','{{.Id}} {{.State.StartedAt}} {{.State.Running}}',cid],stdout=subprocess.PIPE,text=True).stdout.strip()
        result[name]=state
    return result
before=services();env_before=hashlib.sha256((root/'.env').read_bytes()).hexdigest()
tables=('users','subscriptions','devices','vpn_servers','payments','active_screens','alembic_version')
metadata="""SELECT jsonb_build_object(
 'columns',(SELECT jsonb_agg(to_jsonb(c) ORDER BY table_name,ordinal_position) FROM (SELECT table_name,column_name,ordinal_position,column_default,is_nullable,data_type,udt_name FROM information_schema.columns WHERE table_schema='public') c),
 'constraints',(SELECT jsonb_agg(to_jsonb(c) ORDER BY relname,conname) FROM (SELECT r.relname,c.conname,pg_get_constraintdef(c.oid) AS definition FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public') c),
 'indexes',(SELECT jsonb_agg(to_jsonb(i) ORDER BY tablename,indexname) FROM (SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public') i))"""
url=make_url(os.environ['DATABASE_URL']).set(drivername='postgresql').render_as_string(hide_password=False)
started=False
try:
    with psycopg.connect(url) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        snapshot=conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
        source_tables=tuple(r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"))
        assert source_tables==tuple(sorted(tables)), 'Unexpected source table set; review before restore'
        source={}
        for table in tables:
            key='telegram_id' if table=='users' else 'user_id' if table=='active_screens' else 'version_num' if table=='alembic_version' else 'id'
            query=sql.SQL("SELECT count(*),md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY {})::text,'[]')) FROM {} t").format(sql.Identifier(key),sql.Identifier(table))
            source[table]=conn.execute(query).fetchone()
        source_metadata=conn.execute(metadata).fetchone()[0]
        postgres_id=run(['compose','ps','-q','postgres'],stdout=subprocess.PIPE,text=True).stdout.strip()
        image=run(['inspect','--format','{{.Image}}',postgres_id],stdout=subprocess.PIPE,text=True).stdout.strip()
        args=['compose','exec','-T','postgres','pg_dump','-U',os.environ['POSTGRES_USER'],'-d',os.environ['POSTGRES_DB'],'--format=custom','--no-owner','--no-privileges','--snapshot='+snapshot]
        with path.open('xb') as output:
            path.chmod(0o600)
            run(args,stdout=output)
    assert path.stat().st_size>0
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    checksum=path.with_suffix(path.suffix+'.sha256')
    checksum.write_text(digest+'  '+path.name+'\n');checksum.chmod(0o600)
    run(['run','-d','--name',container,'--network','none','--tmpfs','/var/lib/postgresql/data:rw,size=256m,mode=0700','-e','POSTGRES_HOST_AUTH_METHOD=trust',image],stdout=subprocess.PIPE)
    started=True
    for _ in range(40):
        ready=subprocess.run([D,'exec',container,'pg_isready','-U','postgres'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if ready.returncode==0:break
        time.sleep(1)
    else:raise RuntimeError('Temporary PostgreSQL did not become ready')
    run(['exec',container,'createdb','-U','postgres','restore_check'],stdout=subprocess.PIPE)
    with path.open('rb') as archive:
        run(['exec','-i',container,'pg_restore','-U','postgres','-d','restore_check','--exit-on-error','--single-transaction','--no-owner','--no-privileges'],stdin=archive,stdout=subprocess.PIPE)
    def scalar(query):
        result=run(['exec','-i',container,'psql','-X','-A','-t','-U','postgres','-d','restore_check','-v','ON_ERROR_STOP=1','-c',query],stdout=subprocess.PIPE,text=True)
        return result.stdout.strip()
    restored_tables=tuple(scalar("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").splitlines())
    assert restored_tables==source_tables
    restored_metadata=json.loads(scalar(metadata))
    # pg_dump rewrites varchar-array casts into per-element text casts.
    # Normalize only the four simple IN/ANY CHECKs; all other schema fields exact.
    import re
    for schema in (source_metadata, restored_metadata):
        for constraint in schema['constraints']:
            if constraint['conname'] in {'device_platform','device_status','payment_status','subscription_status'}:
                definition=constraint['definition']
                definition=definition.replace('::text[]','').replace('::text','')
                constraint['definition']=re.sub(r'[()\s]', '', definition)
    assert restored_metadata==source_metadata, 'Restored schema differs'
    for table in tables:
        key='telegram_id' if table=='users' else 'user_id' if table=='active_screens' else 'version_num' if table=='alembic_version' else 'id'
        count,hash_value=scalar(f"SELECT count(*),md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY {key})::text,'[]')) FROM {table} t").split('|')
        assert (int(count),hash_value)==source[table], 'Restored records differ'
        print(f'PASS table={table} rows={count}; snapshot checksum matched')
    assert scalar('SELECT version_num FROM alembic_version')=='0002_active_screens'
    print('PASS restored revision=0002_active_screens; columns, constraints and indexes matched')
    print('Backup verified:',path.relative_to(root),'bytes='+str(path.stat().st_size))
finally:
    if started:
        run(['rm','-f',container],stdout=subprocess.PIPE)
        print('Temporary isolated PostgreSQL removed; no working volume used')
assert services()==before, 'Working containers changed externally during verification'
assert hashlib.sha256((root/'.env').read_bytes()).hexdigest()==env_before
print('PASS working bot/PostgreSQL containers not restarted; .env unchanged')
