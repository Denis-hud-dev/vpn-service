"""Only disposable PostgreSQL containers: no host network, ports or disk DB.

Input dump/manifest is delivered by the owner's LOCAL decrypt helper. The
recovery identity never reaches this server. Working containers are read only.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess as sp
import sys
import time

LIB=Path('/usr/local/lib/kudavpn-backup')
BASE=Path('/var/lib/kudavpn-backup')
PATTERN=re.compile(r'kudavpn-restorecheck-[a-f0-9]{12}')
LABELS={'live':'restore_do','pilot':'restore_pilot','website':'restore_website'}


def checked(args,data=None):
    p=sp.run(args,input=data,stdout=sp.PIPE,stderr=sp.PIPE,timeout=120)
    if p.returncode:raise RuntimeError('lab_command_failed')
    return p.stdout


def inspect(name):return json.loads(checked(['docker','inspect',name]))[0]


def state():
    return {name:{k:inspect(name)['State'][k] for k in ['StartedAt','Running']} for name in ['kudavpn-premium-bot-1','kudavpn-premium-postgres-1','kudavpn-xray-xray-1']}


def validate(name):
    if not PATTERN.fullmatch(name):raise RuntimeError('unsafe_container_name')
    info=inspect(name);hc=info['HostConfig']
    if (info['Config'].get('Labels',{}).get('io.kudavpn.backup-restore')!=name
            or hc['NetworkMode']!='none' or hc.get('PortBindings')
            or hc['Memory']>512*1024**2 or hc['Memory']<=0
            or not 0<hc.get('NanoCpus',0)<=500000000 or not 0<hc.get('PidsLimit',0)<=128
            or hc.get('ReadonlyRootfs') is not True
            or hc.get('LogConfig',{}).get('Type')!='none'
            or any(m['Type'] not in ['tmpfs'] for m in info.get('Mounts',[]))
            or not hc.get('Tmpfs',{}).get('/var/lib/postgresql/data')):
        raise RuntimeError('unsafe_container_isolation')
    return info


def meta_path(name):
    if not PATTERN.fullmatch(name):raise RuntimeError('unsafe_container_name')
    return BASE/(name+'.json')


def start(name):
    path=meta_path(name)
    if path.exists():raise RuntimeError('existing_lab_refused')
    available=int(next(x.split()[1] for x in Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemAvailable:')))*1024
    if available<1024**3:raise RuntimeError('lab_memory_guard')
    before=state();image=inspect('kudavpn-premium-postgres-1')['Image']
    path.write_text(json.dumps({'container':name,'before':before,'results':{},'image':image,'created_epoch':time.time()}));path.chmod(0o600)
    checked(['docker','run','-d','--name',name,'--label','io.kudavpn.backup-restore='+name,
             '--network','none','--memory','512m','--cpus','0.5','--pids-limit','128','--read-only',
             '--log-driver','none','--security-opt','no-new-privileges','--tmpfs','/var/lib/postgresql/data:rw,size=256m,mode=0700',
             '--tmpfs','/var/run/postgresql:rw,size=16m','--tmpfs','/tmp:rw,size=32m',
             '-e','POSTGRES_HOST_AUTH_METHOD=trust',image,'postgres','-c','logging_collector=off','-c','log_min_messages=panic','-c','log_min_error_statement=panic'])
    validate(name)
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        p=sp.run(['docker','exec',name,'pg_isready','-U','postgres'],stdout=sp.DEVNULL,stderr=sp.DEVNULL)
        if p.returncode==0:break
        time.sleep(.5)
    else:raise RuntimeError('lab_not_ready')
    if state()!=before:raise RuntimeError('working_container_changed')
    return {'lab':'ready','network':'none','ports':False,'pgdata':'tmpfs','working_unchanged':True}


def load(name,label):
    validate(name);db=LABELS[label]
    checked(['docker','exec',name,'createdb','-U','postgres','-T','template0',db])
    p=sp.run(['docker','exec','-i',name,'pg_restore','-U','postgres','-d',db,'--exit-on-error','--single-transaction','--no-owner','--no-privileges'],
             stdin=sys.stdin.buffer,stdout=sp.PIPE,stderr=sp.PIPE,timeout=180)
    if p.returncode:raise RuntimeError('lab_restore_failed')
    return {'database_label':label,'restored':True}


def metadata_equivalent(expected,actual,canonical):
    """No regex/cast stripping. Only PostgreSQL may canonicalize CHECK DDL.

    Columns, defaults, indexes, constraint identity and validation stay exact.
    Non-CHECK definitions never get this exception.
    """
    if set(expected)!=set(actual):return False
    for key in expected:
        if key!='constraints' and expected[key]!=actual[key]:return False
    left={(x['relname'],x['conname']):x for x in expected['constraints']}
    right={(x['relname'],x['conname']):x for x in actual['constraints']}
    if len(left)!=len(expected['constraints']) or len(right)!=len(actual['constraints']) or set(left)!=set(right):return False
    for key,source in left.items():
        restored=right[key]
        if set(source)!=set(restored):return False
        if any(source[k]!=restored[k] for k in source if k!='definition'):return False
        if source['definition']==restored['definition']:continue
        if not source['definition'].startswith('CHECK (') or not restored['definition'].startswith('CHECK ('):return False
        if not canonical(source):return False
    return True


def canonical_check(name,db,constraint):
    validate(name)
    # The only writable transaction is on the disposable lab table; rollback
    # is unconditional. Compare COMPLETE backend-deparsed clauses, including
    # validation/inheritance modifiers, not merely whether existing rows pass.
    def ident(value):return '"'+value.replace('"','""')+'"'
    def literal(value):return "'"+value.replace("'","''")+"'"
    table='public.'+ident(constraint['relname'])
    temporary='kuda_expected_'+__import__('secrets').token_hex(8)
    sql=('BEGIN; ALTER TABLE '+table+' ADD CONSTRAINT '+ident(temporary)+' '+constraint['definition']+';\n'
         "SELECT json_build_object('equal',"
         '(SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='+literal(table)+'::regclass AND conname='+literal(constraint['conname'])+') = '
         '(SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='+literal(table)+'::regclass AND conname='+literal(temporary)+'))::text;\nROLLBACK;\n')
    command=['docker','exec','-i','-e','PGOPTIONS=-c timezone=UTC -c statement_timeout=60000',name,'psql','-X','-q','-A','-t','-U','postgres','-d',db,'-v','ON_ERROR_STOP=1']
    return json.loads(checked(command,sql.encode())).get('equal') is True


def verify(name,label):
    validate(name);db=LABELS[label];expected=json.load(sys.stdin)
    spec=importlib.util.spec_from_file_location('backup',LIB/'backup.py');backup=importlib.util.module_from_spec(spec);spec.loader.exec_module(backup)
    command=['docker','exec','-i','-e','PGOPTIONS=-c timezone=UTC -c statement_timeout=60000',name,'psql','-X','-q','-A','-t','-U','postgres','-d',db,'-v','ON_ERROR_STOP=1']
    snapshot=backup.Snapshot(command)
    try:
        actual=snapshot.manifest()
        for key in ['revision','tables']:
            if actual[key]!=expected[key]:raise RuntimeError('restored_'+key+'_mismatch')
        if 'users' in actual['tables'] and 'subscriptions' in actual['tables']:
            q='SELECT count(*) FROM subscriptions s LEFT JOIN users u ON u.id=s.user_id WHERE u.id IS NULL'
            if snapshot.query(q)!='0':raise RuntimeError('subscription_owner_orphan')
        if 'payments' in actual['tables']:
            if snapshot.query('SELECT count(*) FROM payments p LEFT JOIN users u ON u.id=p.user_id WHERE u.id IS NULL')!='0':raise RuntimeError('payment_owner_orphan')
            if snapshot.query("SELECT count(*) FROM (SELECT provider,provider_payment_id FROM payments WHERE provider_payment_id IS NOT NULL GROUP BY provider,provider_payment_id HAVING count(*)>1) x")!='0':raise RuntimeError('duplicate_provider_payment')
    finally:snapshot.close()
    # Release read locks BEFORE the separate lab-only DDL transaction.
    canonicalized=[]
    def canonical(row):
        equal=canonical_check(name,db,row)
        if equal:canonicalized.append((row['relname'],row['conname']))
        return equal
    if not metadata_equivalent(expected['metadata'],actual['metadata'],canonical):raise RuntimeError('restored_metadata_mismatch')
    data=json.loads(meta_path(name).read_text())
    data['results'][label]={'passed':True,'revision':actual['revision'],'canonical_checks':len(canonicalized),'table_counts':{k:v['count'] for k,v in actual['tables'].items()}}
    if state()!=data['before']:raise RuntimeError('working_container_changed')
    meta_path(name).write_text(json.dumps(data));meta_path(name).chmod(0o600)
    return data['results'][label]


def finish(name):
    validate(name);path=meta_path(name);data=json.loads(path.read_text())
    if set(data['results'])!=set(LABELS):raise RuntimeError('not_all_databases_verified')
    if state()!=data['before']:raise RuntimeError('working_container_changed')
    checked(['docker','rm','-f',name])  # Only validated, labelled NEW lab container.
    if state()!=data['before']:raise RuntimeError('working_container_changed')
    data['completed_utc']=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
    data['lab_removed']=True;data['working_unchanged']=True
    path.write_text(json.dumps(data));path.chmod(0o600)
    return data


def abort(name):
    info=validate(name);path=meta_path(name);data=json.loads(path.read_text())
    data['exit_state']={k:info['State'].get(k) for k in ['Running','OOMKilled','ExitCode']}
    checked(['docker','rm','-f',name])
    data['lab_removed']=True;data['passed']=False
    data['working_unchanged']=state()==data['before']
    path.write_text(json.dumps(data));path.chmod(0o600)
    return {'lab':'removed_after_failure','working_unchanged':data['working_unchanged']}


def cleanup_expired():
    removed=0
    for path in BASE.glob('kudavpn-restorecheck-*.json'):
        data=json.loads(path.read_text())
        if data.get('lab_removed') or time.time()-data.get('created_epoch',time.time())<45*60:continue
        # validate() refuses ALL working containers, host networks and volumes.
        try:abort(data['container']);removed+=1
        except Exception:print('expired_lab_cleanup_needs_check',file=sys.stderr)
    return {'expired_isolated_labs_removed':removed}


if __name__=='__main__':
    try:
        if os.geteuid()!=0:raise RuntimeError('root_required')
        if sys.argv[1:] == ['cleanup']:
            print(json.dumps(cleanup_expired()));raise SystemExit(0)
        action,name=sys.argv[1:3]
        if action=='start':result=start(name)
        elif action=='load':result=load(name,sys.argv[3])
        elif action=='verify':result=verify(name,sys.argv[3])
        elif action=='finish':result=finish(name)
        elif action=='abort':result=abort(name)
        else:raise RuntimeError('invalid_lab_action')
        print(json.dumps(result,sort_keys=True))
    except Exception as exc:
        code=str(exc) if isinstance(exc,RuntimeError) and re.fullmatch('[a-z_]+',str(exc)) else type(exc).__name__
        try:
            name=sys.argv[2];path=meta_path(name);record=json.loads(path.read_text())
            record['failure']={'action':sys.argv[1],'label':sys.argv[3] if len(sys.argv)>3 and sys.argv[3] in LABELS else None,'code':code}
            path.write_text(json.dumps(record));path.chmod(0o600)
        except Exception:pass
        print(json.dumps({'lab':'failed','code':code}));raise SystemExit(1)
