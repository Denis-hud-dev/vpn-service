"""Install only a new closed review contour, after backup/rollback preparation.

Run as root with immutable release already uploaded and a protected OIDC bundle.
No source DB grants/migrations, existing unit writes or global policy changes.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

BASE=Path('/opt/kudavpn-website-review')
CONFIG=Path('/etc/kudavpn-website-review')
BACKUP=Path('/var/backups/kudavpn-website-review/20261026')
DB='kudavpn_website_test'
UNITS=('kudavpn-website-review.service','kudavpn-website-export.service','kudavpn-website-export.timer')


def run(args,*,input=None,env=None,cwd=None,timeout=120):
    result=subprocess.run(args,input=input,capture_output=True,text=True,env=env,cwd=cwd,timeout=timeout)
    if result.returncode:
        # Dependency and migration errors remain private; no exception/SQL/URL echo.
        with (BACKUP/'installation-errors.log').open('a') as stream:
            stream.write(result.stdout+result.stderr)
        raise RuntimeError('Installation step failed')
    return result.stdout.strip()


def sql(statement,database='postgres'):
    return run(['runuser','-u','postgres','--','psql','-X','-v','ON_ERROR_STOP=1','-At','-d',database],input=statement)


def write(path,data,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    with os.fdopen(fd,'w') as stream:stream.write(data)


def main():
    stage='preflight'
    try:
        if os.geteuid()!=0:raise ValueError()
        revision=sys.argv[1]
        if len(revision)!=40 or any(c not in '0123456789abcdef' for c in revision):raise ValueError()
        release=BASE/('release-'+revision[:7])
        if not (release/'bot/subscription_api/website_main.py').is_file():raise ValueError()
        if not (BACKUP/'baseline.json').is_file():raise ValueError()
        run(['bash',str(BACKUP/'rollback.sh')],env={**os.environ,'CHECK_ONLY':'1'})
        if run(['ss','-ltnH','sport = :20443']):raise ValueError()
        if CONFIG.exists() or (BASE/'current').exists():raise ValueError()
        if sql("SELECT datname FROM pg_database WHERE datname='kudavpn_website_test';"):raise ValueError()
        if sql("SELECT rolname FROM pg_roles WHERE rolname='kudavpn_website_test';"):raise ValueError()
        if subprocess.run(['id',DB],capture_output=True).returncode==0:raise ValueError()
        stage='isolated_identity'
        run(['useradd','--system','--no-create-home','--home-dir','/nonexistent','--shell','/usr/sbin/nologin',DB])
        CONFIG.mkdir(mode=0o700)
        bundle=BASE/'oidc-upload.json'
        credentials=json.loads(bundle.read_text())
        origin='https://r1276248.s2578.vm.mydomain.zone:20443'
        if credentials.get('origin')!=origin or credentials.get('redirect_uri')!=origin+'/auth/callback':raise ValueError()
        pid=run(['systemctl','show','-P','MainPID','kudavpn-telegram-pilot-bot.service'])
        env=dict(row.split('=',1) for row in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in row)
        if str(credentials['client_id'])!=env['BOT_TOKEN'].split(':',1)[0]:raise ValueError('OIDC must belong to test bot')
        owner=int(env['PILOT_TRIAL_OWNER_ID'])
        if owner<=0:raise ValueError()
        write(CONFIG/'oidc.json',json.dumps(credentials))
        bundle.unlink()
        write(CONFIG/'owner.json',json.dumps({'owner_id':owner}))
        pid=run(['systemctl','show','-P','MainPID','kudavpn-test-checkout.service'])
        old=dict(row.split('=',1) for row in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in row)
        for source,target in [('CHECKOUT_TLS_CERT','tls.crt'),('CHECKOUT_TLS_KEY','tls.key')]:
            shutil.copyfile(old[source],CONFIG/target);(CONFIG/target).chmod(0o600)
        stage='isolated_database'
        # Peer login: no password/key for the database exists at all.
        sql('CREATE ROLE kudavpn_website_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 12;')
        sql('CREATE DATABASE kudavpn_website_test OWNER postgres;')
        sql('REVOKE ALL ON DATABASE kudavpn_website_test FROM PUBLIC; GRANT CONNECT ON DATABASE kudavpn_website_test TO kudavpn_website_test;')
        migration_env={**os.environ,'DATABASE_URL':'postgresql+psycopg:///kudavpn_website_test?host=/var/run/postgresql','PYTHONDONTWRITEBYTECODE':'1'}
        run(['runuser','-u','postgres','--',str(BASE/'venv/bin/python'),'-m','alembic','upgrade','head'],env=migration_env,cwd=release)
        sql('REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT USAGE ON SCHEMA public TO kudavpn_website_test; '
            'GRANT SELECT,INSERT,DELETE ON web_login_flows,review_sessions TO kudavpn_website_test; '
            'GRANT SELECT ON alembic_version TO kudavpn_website_test;',DB)
        # A new role must not gain rights in the canonical source, even via PUBLIC.
        privileges=sql("SELECT coalesce(bool_or(has_table_privilege('kudavpn_website_test',c.oid,'SELECT,INSERT,UPDATE,DELETE')),false) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r';",'kudavpn_telegram_pilot')
        if privileges!='f':raise ValueError('Source database must be inaccessible')
        if run(['runuser','-u',DB,'--','psql','-X','-At','-d',DB,'-c','SELECT current_user'])!=DB:raise ValueError()
        # Restrict application writes to browser flows/sessions, not even empty canonical tables.
        if sql("SELECT has_table_privilege('kudavpn_website_test','users','INSERT') OR has_table_privilege('kudavpn_website_test','subscriptions','UPDATE');",DB)!='f':raise ValueError()
        with (BACKUP/'auth-empty.dump').open('wb') as stream:
            subprocess.run(['runuser','-u','postgres','--','pg_dump','-Fc',DB],stdout=stream,stderr=subprocess.PIPE,check=True,timeout=30)
        run(['pg_restore','--list',str(BACKUP/'auth-empty.dump')])
        run(['pg_restore','--file=/dev/null',str(BACKUP/'auth-empty.dump')])
        stage='isolated_configuration'
        export=Path('/var/lib/kudavpn-website-export')
        if export.exists():raise ValueError()
        export.mkdir(mode=0o750);shutil.chown(export,user='root',group=DB)
        values={'KUDAVPN_SITE_MODE':'review-test','SITE_PUBLIC_ORIGIN':origin,
                'SITE_APPROVED_PUBLIC_REVIEW':'1','SITE_PORT':'20443',
                'DATABASE_URL':'postgresql+psycopg://kudavpn_website_test@/kudavpn_website_test?host=/var/run/postgresql',
                'SITE_REVIEW_OWNER_FILE':'/run/credentials/kudavpn-website-review.service/owner.json',
                'SITE_OIDC_BUNDLE_FILE':'/run/credentials/kudavpn-website-review.service/oidc.json',
                'SITE_TLS_CERT':'/run/credentials/kudavpn-website-review.service/tls.crt',
                'SITE_TLS_KEY':'/run/credentials/kudavpn-website-review.service/tls.key',
                'SITE_SNAPSHOT_FILE':str(export/'snapshot.json'),'PYTHONDONTWRITEBYTECODE':'1'}
        write(CONFIG/'site.env',''.join(k+'='+json.dumps(v)+'\n' for k,v in values.items()))
        (BASE/'current').symlink_to(release)
        for name in UNITS:
            destination=Path('/etc/systemd/system')/name
            if destination.exists():raise ValueError()
            shutil.copyfile(release/'deploy/website-review'/name,destination);destination.chmod(0o644)
        run(['systemd-analyze','verify',*[str(Path('/etc/systemd/system')/name) for name in UNITS]])
        shutil.copytree(CONFIG,BACKUP/'prepared-config',copy_function=shutil.copy2)
        stage='snapshot_start'
        run(['systemctl','daemon-reload'])
        run(['systemctl','start','kudavpn-website-export.service'],timeout=30)
        stage='closed_website_start'
        run(['systemctl','enable','--now','kudavpn-website-export.timer','kudavpn-website-review.service'])
        # TLS cert/hostname validation is mandatory. No -k or readiness race.
        import time
        ready=False
        for _ in range(40):
            result=subprocess.run(['curl','-fsS','--max-time','2','--resolve',
                'r1276248.s2578.vm.mydomain.zone:20443:127.0.0.1','-o','/dev/null',origin+'/ready'],capture_output=True)
            if result.returncode==0:ready=True;break
            time.sleep(1)
        if not ready:raise RuntimeError('Readiness not reached')
        stage='protected_state_verification'
        run(['python3',str(BACKUP/'baseline.py'),'verify',str(BACKUP/'baseline.json')])
        print('website_deploy=ready_owner_only_read_only; protected_services_unchanged; rollback_prepared')
    except BaseException:
        print('website_deploy=failed stage='+stage+'; attempting_new_components_only_rollback',flush=True)
        subprocess.run(['bash',str(BACKUP/'rollback.sh')],check=False)
        raise SystemExit(1) from None


if __name__=='__main__':main()
