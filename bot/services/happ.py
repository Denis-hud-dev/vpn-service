"""Personal feed and node convergence. No real node adapter is enabled by default."""
import asyncio
import hashlib
import re
import secrets
from urllib.parse import urlsplit, urlencode, quote
from uuid import UUID
from typing import Protocol
from sqlalchemy import select, exists
from bot.db.models import Subscription, SubscriptionToken, VPNNode, NodeAccess, Payment, SubscriptionHistory
from bot.services.subscriptions import utcnow, as_utc
from datetime import timedelta

NODE_LEASE_SECONDS = 120
NODE_RENEW_SECONDS = 60
RECONCILE_BATCH_SIZE = 128
MAX_CONCURRENT_RECONCILES = 16


class AccessDenied(ValueError):
    pass


def token_digest(token):
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise AccessDenied('Subscription unavailable')
    return hashlib.sha256(token.encode()).hexdigest()


async def entitled(session, sub):
    return bool(sub.status == 'active' and sub.expires_at and as_utc(sub.expires_at) > utcnow()
        and await session.scalar(select(exists().where(
            SubscriptionHistory.subscription_id == sub.id,
            SubscriptionHistory.payment_id == Payment.id,
            Payment.status == 'succeeded', Payment.subscription_id == sub.id,
            Payment.user_id == sub.user_id))))


class NodeGateway(Protocol):
    async def apply(self, node: VPNNode, credential: UUID, *, enabled: bool, expires_at, revision: int):
        """Idempotent remote operation. ACK only after enforcement on the node.

        The agent must expire credentials independently and terminate/reject
        sessions when revoked. Do not treat request acceptance as enforcement.
        """
        ...


class DisabledNodeGateway:
    async def apply(self, node, credential: UUID, *, enabled: bool, expires_at, revision: int):
        raise RuntimeError('VPN node adapter is not configured')


class HappService:
    def __init__(self, sessions, public_url):
        url = urlsplit(public_url)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ('', '/')):
            raise ValueError('Subscription origin must be HTTPS')
        self.sessions, self.public_url = sessions, public_url.rstrip('/')

    async def issue(self, subscription_id):
        async with self.sessions.begin() as session:
            sub = await session.scalar(select(Subscription).where(Subscription.id == subscription_id).with_for_update())
            if sub is None or not await entitled(session, sub):
                raise AccessDenied('Active paid subscription required')
            previous = await session.scalar(select(SubscriptionToken).where(SubscriptionToken.subscription_id == sub.id))
            if previous and not previous.revoked:
                raise AccessDenied('Link already issued; revoke before rotation')
            if previous and await session.scalar(select(exists().where(NodeAccess.subscription_id == sub.id, NodeAccess.revocation_confirmed.is_(False)))):
                raise AccessDenied('Node revocation must finish before rotation')
            token = secrets.token_urlsafe(32)
            if previous:
                previous.digest, previous.revoked = token_digest(token), False
                rows = (await session.scalars(select(NodeAccess).where(NodeAccess.subscription_id == sub.id))).all()
                from uuid import uuid4
                for row in rows:
                    row.credential, row.applied_expires_at = uuid4(), None
                    row.revocation_confirmed = False
                    row.enabled, row.desired_enabled = False, False
                    row.desired_expires_at, row.lease_revision = None, 0
            else:
                session.add(SubscriptionToken(subscription_id=sub.id, digest=token_digest(token), revoked=False))
            return self.public_url + '/s/' + token

    async def revoke(self, subscription_id):
        async with self.sessions.begin() as session:
            await session.scalar(select(Subscription).where(Subscription.id == subscription_id).with_for_update())
            token = await session.scalar(select(SubscriptionToken).where(SubscriptionToken.subscription_id == subscription_id))
            if token:
                token.revoked = True
            else:
                # A revocation before first link issuance must still prevent the
                # background reconciler from provisioning this subscription.
                token = secrets.token_urlsafe(32)
                session.add(SubscriptionToken(subscription_id=subscription_id,
                                              digest=token_digest(token), revoked=True))

    async def feed(self, token):
        digest = token_digest(token)
        async with self.sessions() as session:
            row = await session.scalar(select(SubscriptionToken).where(SubscriptionToken.digest == digest, SubscriptionToken.revoked.is_(False)))
            sub = await session.get(Subscription, row.subscription_id) if row else None
            if sub is None or not await entitled(session, sub):
                raise AccessDenied('Subscription unavailable')
            rows = (await session.execute(select(VPNNode, NodeAccess).join(NodeAccess, NodeAccess.node_id == VPNNode.id).where(
                NodeAccess.subscription_id == sub.id, NodeAccess.enabled.is_(True), VPNNode.enabled.is_(True))
                .order_by(VPNNode.name))).all()
            lines = []
            for node, access in rows:
                # Never advertise stale credentials or an expired node lease.
                if not access.applied_expires_at or as_utc(access.applied_expires_at) <= utcnow():continue
                params = urlencode({'encryption':'none','security':'reality','type':'tcp',
                    'flow':'xtls-rprx-vision','fp':'chrome','sni':node.server_name,
                    'pbk':node.public_key,'sid':node.short_id})
                host = '['+node.hostname+']' if ':' in node.hostname else node.hostname
                lines.append(f'vless://{access.credential}@{host}:{node.port}?{params}#{quote(node.name, safe="")}')
            if not lines:
                raise AccessDenied('Subscription unavailable')
            return '\n'.join(lines)+'\n', int(as_utc(sub.expires_at).timestamp())


class NodeReconciler:
    """Idempotent gateway must enforce lease expiry on the node, even offline.

    Gateway acknowledgement is required before advertising access. Exceptions
    roll back DB state, retry next tick. Gateway calls must be safe to repeat.
    Nodes are disabled rather than deleted, so old credentials can be removed.
    """
    def __init__(self, sessions, gateway=None, *, batch_size=RECONCILE_BATCH_SIZE,
                 max_concurrent=MAX_CONCURRENT_RECONCILES):
        if batch_size < 1 or max_concurrent < 1:
            raise ValueError('Reconciliation limits must be positive')
        self.sessions, self.gateway = sessions, gateway or DisabledNodeGateway()
        self.batch_size, self.max_concurrent = batch_size, max_concurrent

    async def reconcile(self, subscription_id):
        # Persist a versioned intent before the remote call. A late old request
        # cannot undo a revoke; the agent rejects lower revisions per credential.
        async with self.sessions.begin() as session:
            sub = await session.scalar(select(Subscription).where(Subscription.id == subscription_id).with_for_update())
            if sub is None:
                return
            token = await session.scalar(select(SubscriptionToken).where(SubscriptionToken.subscription_id == sub.id))
            # A verified payment is enough to provision access. The HTTPS link
            # may be issued later, while an explicit revoke remains authoritative.
            allowed = bool((token is None or not token.revoked) and await entitled(session, sub))
            nodes = (await session.scalars(select(VPNNode).order_by(VPNNode.id))).all()
            now = utcnow()
            for node in nodes:
                access = await session.scalar(select(NodeAccess).where(
                    NodeAccess.subscription_id == sub.id, NodeAccess.node_id == node.id))
                desired = allowed and node.enabled
                if access is None:
                    if not desired:
                        continue
                    access = NodeAccess(subscription_id=sub.id, node_id=node.id,
                                        enabled=False, desired_enabled=False)
                    session.add(access)
                    await session.flush()
                desired_expiry = min(as_utc(sub.expires_at), now + timedelta(seconds=NODE_LEASE_SECONDS)) if desired else None
                old_expiry = as_utc(access.desired_expires_at) if access.desired_expires_at else None
                renewal_due = desired and (old_expiry is None or old_expiry <= now + timedelta(seconds=NODE_RENEW_SECONDS))
                if access.desired_enabled != desired or renewal_due:
                    access.lease_revision += 1
                    access.desired_enabled = desired
                    access.desired_expires_at = desired_expiry
                    if desired:
                        access.revocation_confirmed = False
        # Apply durable intents. The worker retries after process/network failure.
        async with self.sessions.begin() as session:
            sub = await session.scalar(select(Subscription).where(Subscription.id == subscription_id).with_for_update())
            if sub is None:
                return
            rows = (await session.execute(select(NodeAccess, VPNNode).join(
                VPNNode, VPNNode.id == NodeAccess.node_id).where(NodeAccess.subscription_id == sub.id)
                .order_by(VPNNode.id).with_for_update())).all()
            for access, node in rows:
                desired = access.desired_enabled
                expires = as_utc(access.desired_expires_at) if access.desired_expires_at else None
                if desired and expires and expires <= utcnow():
                    # A stale lease intent may never reactivate an expired right.
                    continue
                if (access.enabled == desired and
                    ((not desired and access.revocation_confirmed) or
                     (desired and access.applied_expires_at and
                      as_utc(access.applied_expires_at) == expires))):
                    continue
                await asyncio.wait_for(self.gateway.apply(node, access.credential,
                    enabled=desired, expires_at=expires, revision=access.lease_revision), timeout=5)
                access.enabled = desired
                access.applied_expires_at = expires if desired else None
                access.revocation_confirmed = not desired

    async def tick(self):
        # Keyset paging avoids loading every customer into memory. Process only
        # one bounded batch at a time so slow/offline nodes cannot serialize the
        # entire population or spawn an unbounded number of gateway requests.
        last_id = None
        while True:
            async with self.sessions() as session:
                query = select(Subscription.id).order_by(Subscription.id).limit(self.batch_size)
                if last_id is not None:
                    query = query.where(Subscription.id > last_id)
                ids = (await session.scalars(query)).all()
            if not ids:
                return
            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def process(subscription_id):
                async with semaphore:
                    try:
                        await self.reconcile(subscription_id)
                    except Exception:
                        # No exception strings, node credentials or identifiers in logs.
                        import logging
                        logging.getLogger(__name__).warning('Node reconciliation failed; retry scheduled')

            await asyncio.gather(*(process(sid) for sid in ids))
            last_id = ids[-1]
