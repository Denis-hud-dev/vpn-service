"""Fixed canonical pilot reader. No commands, SQL or identities from HTTP.

Run separately from the unprivileged website. Source credentials never enter
the website environment. PostgreSQL rejects writes in every export transaction.
"""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from bot.db.models import Payment, Subscription, SubscriptionToken, User
from bot.services import BILLABLE_TARIFFS
from bot.services.happ import HappService, entitled
from bot.services.subscriptions import as_utc, utcnow


def label(tariff):
    plan = BILLABLE_TARIFFS.get(tariff)
    return plan.label if plan else ('Пробный доступ · 24 часа' if tariff == 'pilot_trial_24h' else 'Название тарифа не сохранено')


async def collect(session, happ, owner):
    """SELECT only. Reuse existing entitlement, node lease and HMAC rules."""
    user = await session.scalar(select(User).where(User.telegram_id == owner))
    result = {'version':1, 'owner_id':owner, 'owner_known':user is not None,
              'timestamp':time.time(), 'account':{'status':'inactive','expires_at':None,
                  'display_name':None,'tariff':None,'nodes':[]}, 'payments':[]}
    if user is None:
        return result
    account = result['account']
    account['display_name'] = ' '.join(p for p in (user.first_name,user.last_name) if p)[:256]
    sub = await session.scalar(select(Subscription).where(Subscription.user_id == user.id))
    if sub:
        account.update(status=str(sub.status), tariff=label(sub.tariff_id),
                       expires_at=as_utc(sub.expires_at).isoformat() if sub.expires_at else None)
        if sub.status == 'active' and (not sub.expires_at or as_utc(sub.expires_at) <= utcnow()):
            account['status'] = 'expired'
        if await entitled(session, sub, trial_owner_id=owner):
            nodes = await happ._live_nodes(session, sub.id)
            token = await session.scalar(select(SubscriptionToken).where(SubscriptionToken.subscription_id == sub.id))
            if nodes and token and not token.revoked and token.derivation_nonce:
                # Reproduce the existing bearer, never call issue()/issue_for_user().
                bearer = happ._derived_token(sub.id, token.derivation_nonce)
                if not hmac.compare_digest(hashlib.sha256(bearer.encode()).hexdigest(), token.digest):
                    raise ValueError('Existing feed verification failed')
                account['nodes'] = [node.name for node, _ in nodes]
                account['feed_url'] = happ.public_url+'/s/'+bearer
    payments = (await session.scalars(select(Payment).where(Payment.user_id == user.id)
                                     .order_by(Payment.created_at.desc(),Payment.id.desc()).limit(30))).all()
    result['payments'] = [{'tariff':label(p.tariff_id), 'amount_kopecks':p.amount_kopecks,
                          'currency':p.currency, 'status':str(p.status),
                          'created_at':as_utc(p.created_at).isoformat(),
                          'test':True, 'receipt':'test_not_required','receipt_error':None} for p in payments]
    return result


def source_environment():
    """Read only the approved isolated bot, not production/Monitor environments."""
    pid = subprocess.run(['systemctl','show','-P','MainPID','kudavpn-telegram-pilot-bot.service'],
                         check=True,capture_output=True,text=True,timeout=5).stdout.strip()
    if not pid.isdecimal() or int(pid) < 2:
        raise ValueError('Pilot service unavailable')
    values = dict(row.split('=',1) for row in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in row)
    url = make_url(values['DATABASE_URL'])
    if (url.drivername != 'postgresql+psycopg' or url.host != '127.0.0.1'
            or url.database != 'kudavpn_telegram_pilot' or url.username != 'kudavpn_pilot'):
        raise ValueError('Only canonical isolated pilot source allowed')
    owner = int(values['PILOT_TRIAL_OWNER_ID'])
    if owner <= 0 or values['SUBSCRIPTION_PUBLIC_URL'] != 'https://r1276248.s2578.vm.mydomain.zone:18443':
        raise ValueError('Trusted pilot configuration required')
    return values, owner


async def snapshot(values, owner):
    engine = create_async_engine(values['DATABASE_URL'],poolclass=NullPool,hide_parameters=True,
        connect_args={'connect_timeout':5,'options':'-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000'})
    sessions = async_sessionmaker(engine,expire_on_commit=False,autoflush=False)
    happ = HappService(sessions,values['SUBSCRIPTION_PUBLIC_URL'],link_key=values['HAPP_LINK_SECRET'],
                       trial_owner_id=owner,trial_node_port=10443)
    try:
        async with sessions.begin() as session:
            await session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            if await session.scalar(text('SHOW transaction_read_only')) != 'on':
                raise ValueError('Read-only transaction required')
            return await collect(session,happ,owner)
    finally:
        await engine.dispose()


def publish(path, data):
    """Root-owned directory; website group can read but cannot replace cache."""
    path = Path(path)
    parent = path.parent
    if (os.geteuid() != 0 or not path.is_absolute() or parent.is_symlink() or path.is_symlink()
            or parent.stat().st_uid != 0 or parent.stat().st_mode & 0o022):
        raise ValueError('Root-owned non-writable export directory required')
    fd, temporary = tempfile.mkstemp(dir=parent,prefix='.snapshot-')
    try:
        with os.fdopen(fd,'w') as stream:
            os.fchmod(stream.fileno(),0o640)
            os.fchown(stream.fileno(),0,parent.stat().st_gid)
            json.dump(data,stream,ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify-only',action='store_true')
    parser.add_argument('--output')
    args = parser.parse_args()
    if bool(args.verify_only) == bool(args.output):
        raise SystemExit('Choose verification or private export')
    # Avoid SQL, dependency and traceback logs containing source parameters.
    import logging
    logging.disable(logging.CRITICAL)
    try:
        values, owner = source_environment()
        data = asyncio.run(snapshot(values,owner))
        if args.output:
            publish(args.output,data)
        print(json.dumps({'read_only':True,'owner_found':data['owner_known'],
                          'status':data['account']['status'],'history_rows':len(data['payments']),
                          'existing_feed_ready':bool(data['account'].get('feed_url'))}))
    except Exception:
        raise SystemExit('Canonical read-only export unavailable; no source changes performed') from None


if __name__ == '__main__':
    main()
