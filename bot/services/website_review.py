"""Owner-only sessions plus a read-only cache of canonical pilot data.

No canonical DB URL, payment gateway, agent client, UUID or issuance method is
available to the website process. A separate trusted fixed exporter owns reads.
"""
from datetime import timedelta
import json
import math
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from sqlalchemy import delete, select

from bot.db.models import ReviewSession
from bot.services import PILOT_TARIFFS
from bot.services.subscriptions import as_utc
from bot.services.web_checkout import OrderUnavailable
from bot.services.website_auth import WebsiteAuth, digest


class ReviewAuth(WebsiteAuth):
    session_model = ReviewSession

    def __init__(self, sessions, oidc, *, allowed_telegram_ids, **kwargs):
        super().__init__(sessions, oidc, allowed_telegram_ids=allowed_telegram_ids, **kwargs)
        if len(self.allowed_telegram_ids) != 1:
            raise ValueError('Exactly one review owner required')

    async def finish(self, state, browser, code):
        identity = await self.authenticate_code(state, browser, code)
        return await self.new_session(identity['telegram_id'])

    async def new_session(self, owner):
        if type(owner) is not int or not self.permitted(owner):
            raise PermissionError('Authentication unavailable')
        token = secrets.token_urlsafe(32)
        async with self.sessions.begin() as session:
            await session.execute(delete(ReviewSession).where(ReviewSession.expires_at <= self.clock()))
            session.add(ReviewSession(digest=digest(token), owner_id=owner,
                                      expires_at=self.clock()+timedelta(hours=1)))
        return token

    async def owner(self, token):
        async with self.sessions() as session:
            owner = await session.scalar(select(ReviewSession.owner_id).where(
                ReviewSession.digest == digest(token), ReviewSession.expires_at > self.clock()))
        if owner is None or not self.permitted(owner):
            raise PermissionError('Authentication unavailable')
        return owner

    async def logout(self, token):
        async with self.sessions.begin() as session:
            await session.execute(delete(ReviewSession).where(ReviewSession.digest == digest(token)))


class CanonicalDataUnavailable(RuntimeError):
    pass


class ReviewSnapshot:
    def __init__(self, path, owner_id, feed_origin, *, clock=time.time, root_owned=True):
        self.path, self.owner_id, self.feed_origin, self.clock = Path(path), owner_id, feed_origin, clock
        self.root_owned = root_owned
        parsed = urlsplit(feed_origin)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('Trusted feed origin required')

    def read(self, owner):
        if owner != self.owner_id:
            raise PermissionError('Account unavailable')
        try:
            if (not self.path.is_absolute() or self.path.is_symlink() or self.path.parent.is_symlink()
                    or self.path.stat().st_mode & 0o777 not in {0o600,0o640,0o400,0o440}
                    or self.path.parent.stat().st_mode & 0o022
                    or self.root_owned and (self.path.stat().st_uid != 0 or self.path.parent.stat().st_uid != 0)
                    or self.path.stat().st_size > 65536):
                raise ValueError()
            data = json.loads(self.path.read_text())
            stamp = data['timestamp']
            if (type(stamp) not in {int,float} or not math.isfinite(stamp)
                    or not self.clock()-120 < stamp <= self.clock()+30
                    or data['version'] != 1 or data['owner_id'] != owner or data['owner_known'] is not True):
                raise ValueError()
            account = data['account']
            if account['status'] not in {'active','expired','inactive','pending','blocked'}:
                raise ValueError()
            for field in ('display_name','tariff'):
                if account.get(field) is not None and (not isinstance(account[field],str) or len(account[field])>256):
                    raise ValueError()
            expiry = None
            if account.get('expires_at'):
                from datetime import datetime
                expiry = as_utc(datetime.fromisoformat(account['expires_at'])).timestamp()
            if account['status'] == 'active' and expiry is None:
                raise ValueError()
            nodes = account['nodes']
            if not isinstance(nodes,list) or len(nodes)>64 or any(not isinstance(n,str) or len(n)>128 for n in nodes):
                raise ValueError()
            feed = account.get('feed_url')
            if feed:
                url = urlsplit(feed)
                if (url.scheme+'://'+url.netloc != self.feed_origin or url.query or url.fragment
                        or not re.fullmatch(r'/s/[A-Za-z0-9_-]{43}',url.path)):
                    raise ValueError()
            if account['status'] == 'active' and expiry <= self.clock():
                account['status'] = 'expired'
            if account['status'] != 'active' or not nodes:
                account.pop('feed_url',None)
            payments = data['payments']
            if not isinstance(payments,list) or len(payments)>30:
                raise ValueError()
            for row in payments:
                if (type(row['amount_kopecks']) is not int or row['amount_kopecks']<=0 or row['currency']!='RUB'
                        or row['status'] not in {'pending','succeeded','canceled','refunded'} or row['test'] is not True
                        or not isinstance(row['tariff'],str) or len(row['tariff'])>128
                        or row['receipt'] != 'test_not_required'):
                    raise ValueError()
                from datetime import datetime
                as_utc(datetime.fromisoformat(row['created_at']))
                if set(row) - {'tariff','amount_kopecks','currency','status','created_at','test','receipt','receipt_error'}:
                    raise ValueError()
            return data
        except Exception:
            raise CanonicalDataUnavailable('Canonical data temporarily unavailable') from None


class ReadOnlyReview:
    read_only = True

    def __init__(self, snapshot, origin):
        self.snapshot, self.origin = snapshot, origin

    def plans(self):
        return [{'id':p.id,'label':p.label,'amount_kopecks':p.price_rub*100,'months':p.months,
                 'style':'success' if p.months==1 else 'primary'} for p in PILOT_TARIFFS.values()]

    async def account(self, owner):
        data = self.snapshot.read(owner)
        account = data['account']
        result = {key:account.get(key) for key in ('status','tariff','expires_at','nodes','display_name')}
        result.update(read_only=True, source='canonical_pilot', checked_at=data['timestamp'],
                      access_pending=account['status']=='active' and not account.get('feed_url'))
        if account.get('feed_url'):
            result['connect_url'] = self.origin+'/connect'
        return result

    async def connection(self, owner):
        data = self.snapshot.read(owner)
        account = data['account']
        if not account.get('feed_url'):
            raise PermissionError('Connection unavailable')
        from datetime import datetime
        return account['feed_url'],int(as_utc(datetime.fromisoformat(account['expires_at'])).timestamp())

    async def history(self, owner):
        return self.snapshot.read(owner)['payments']

    async def reconcile(self, owner):
        self.snapshot.read(owner)  # Compatibility boundary; never writes anything.

    async def latest(self, owner):
        self.snapshot.read(owner)
        return None

    async def create(self, owner, tariff):
        self.snapshot.read(owner)
        if tariff not in PILOT_TARIFFS:
            raise ValueError('Unknown tariff')
        plan = PILOT_TARIFFS[tariff]
        return {'id':'selection-'+plan.id,'state':'selection_only','label':plan.label,
                'amount_kopecks':plan.price_rub*100,'currency':'RUB','months':plan.months,'test':True}

    async def state(self, owner, order):
        if not isinstance(order,str) or not order.startswith('selection-'):
            raise OrderUnavailable()
        return await self.create(owner,order.removeprefix('selection-'))

    async def refresh(self, owner, order):
        return await self.state(owner,order)
