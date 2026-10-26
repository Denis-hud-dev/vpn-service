"""Read existing token/nonce only; never issue/rotate a link or modify the DB."""
import asyncio
import base64
import hashlib
import hmac
import os
from pathlib import Path
import subprocess

from sqlalchemy import select, text
from bot.db import Database
from bot.db.models import Subscription, SubscriptionToken, User


async def main():
    pid = subprocess.check_output(['systemctl', 'show', '-P', 'MainPID',
                                  'kudavpn-telegram-pilot-bot.service'], text=True).strip()
    values = dict(x.split('=', 1) for x in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in x)
    db = Database(values['DATABASE_URL'])
    try:
        async with db.sessions.begin() as session:
            await session.execute(text('SET TRANSACTION READ ONLY'))
            row = (await session.execute(select(Subscription, SubscriptionToken)
                .join(User, User.id == Subscription.user_id)
                .join(SubscriptionToken, SubscriptionToken.subscription_id == Subscription.id)
                .where(User.telegram_id == int(values['PILOT_TRIAL_OWNER_ID'])))).one_or_none()
            if row is None or row[1].revoked or not row[1].derivation_nonce:
                print('feed_probe=not_available')
                return
            sub, token = row
            key = base64.urlsafe_b64decode(values['HAPP_LINK_SECRET']+'=')
            raw = hmac.new(key, b'kudavpn-happ-v1:'+sub.id.bytes+bytes.fromhex(token.derivation_nonce), hashlib.sha256).digest()
            bearer = base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
            assert hashlib.sha256(bearer.encode()).hexdigest() == token.digest
            target = Path('/etc/kudavpn-monitor/feed-probe-url')
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(values['SUBSCRIPTION_PUBLIC_URL']+'/s/'+bearer+'\n')
        print('feed_probe=prepared_read_only')
    finally:
        await db.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as error:
        print('feed_probe=not_available category='+type(error).__name__)
        raise SystemExit(1) from None
