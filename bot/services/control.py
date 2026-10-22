"""Inactive-by-default profile registry and conservative Xray counter accounting.

A profile is a credential slot, never proof of a unique physical device.
Provisioning and revocation are intentionally not wired to the live bot: the
current agent restarts its entire Xray process on revoke.
"""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from sqlalchemy import func, select
from bot.db.models import DeviceProfile, NodeAccess, ProfileTraffic, Subscription, User, VPNNode
from bot.services.subscriptions import as_utc, utcnow


class ControlDenied(ValueError):
    pass


@dataclass(frozen=True)
class ProfileView:
    id: UUID
    name: str
    platform: str
    state: str
    last_seen_at: datetime | None


class ControlService:
    def __init__(self, sessions):
        self.sessions = sessions

    async def list_profiles(self, telegram_id: int) -> list[ProfileView]:
        async with self.sessions() as session:
            rows = (await session.scalars(select(DeviceProfile)
                .join(Subscription, Subscription.id == DeviceProfile.subscription_id)
                .join(User, User.id == Subscription.user_id)
                .where(User.telegram_id == telegram_id, DeviceProfile.state != 'revoked')
                .order_by(DeviceProfile.created_at, DeviceProfile.id))).all()
            return [ProfileView(row.id, row.name, row.platform, row.state,
                                as_utc(row.last_seen_at) if row.last_seen_at else None)
                    for row in rows]

    async def reserve_profile(self, telegram_id: int, node_id: UUID, name: str,
                              platform: str) -> UUID:
        """Reserve one inactive key slot; never grant VPN access here."""
        if any(ord(ch) < 32 for ch in name):
            raise ControlDenied('Invalid profile name')
        name = name.strip()
        if not name or len(name) > 64:
            raise ControlDenied('Invalid profile name')
        if platform not in {'ios', 'android', 'windows', 'macos'}:
            raise ControlDenied('Invalid platform')
        async with self.sessions.begin() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
            if user is None:
                raise ControlDenied('Subscription unavailable')
            sub = await session.scalar(select(Subscription).where(
                Subscription.user_id == user.id).with_for_update())
            if (sub is None or sub.status != 'active' or sub.expires_at is None
                    or as_utc(sub.expires_at) <= utcnow()):
                raise ControlDenied('Active subscription required')
            node = await session.get(VPNNode, node_id)
            if node is None or not node.enabled:
                raise ControlDenied('Node unavailable')
            legacy = await session.scalar(select(func.count()).select_from(NodeAccess).where(
                NodeAccess.subscription_id == sub.id))
            if legacy:
                # A shared UUID already represents an unknown number of clients.
                # Do not silently mix it with counted per-profile credentials.
                raise ControlDenied('Legacy shared access requires a separate migration')
            used = await session.scalar(select(func.count()).select_from(DeviceProfile).where(
                DeviceProfile.subscription_id == sub.id, DeviceProfile.state != 'revoked'))
            if used >= sub.device_limit:
                raise ControlDenied('Profile limit reached')
            profile = DeviceProfile(subscription_id=sub.id, node_id=node.id,
                                    name=name, platform=platform, state='pending',
                                    lease_revision=0)
            session.add(profile)
            await session.flush()
            return profile.id

    async def traffic(self, telegram_id: int, profile_id: UUID) -> int | None:
        """Only complete, owner-scoped samples may be shown as actual usage."""
        async with self.sessions() as session:
            row = await session.scalar(select(ProfileTraffic)
                .join(DeviceProfile, DeviceProfile.id == ProfileTraffic.profile_id)
                .join(Subscription, Subscription.id == DeviceProfile.subscription_id)
                .join(User, User.id == Subscription.user_id)
                .where(User.telegram_id == telegram_id, DeviceProfile.id == profile_id,
                       DeviceProfile.state != 'revoked'))
            return row.total_up + row.total_down if row and row.complete else None


def account_sample(counter: ProfileTraffic, *, up: int, down: int,
                   generation: str, sampled_at: datetime) -> None:
    """Idempotent monotonic deltas; reset/gap makes total undisplayable.

    A source generation must identify one Xray process. No sample is accepted
    out of order. Missing stats never become zero. The first sample establishes
    a baseline; only subsequent deltas are counted.
    """
    if up < 0 or down < 0 or not generation or len(generation) > 64:
        raise ValueError('Invalid traffic sample')
    sampled_at = as_utc(sampled_at)
    if counter.sampled_at and sampled_at <= as_utc(counter.sampled_at):
        return
    if counter.source_generation is None:
        counter.last_up, counter.last_down = up, down
        counter.source_generation, counter.sampled_at = generation, sampled_at
        return
    if generation != counter.source_generation or up < counter.last_up or down < counter.last_down:
        counter.complete = False
        counter.last_up, counter.last_down = up, down
        counter.source_generation, counter.sampled_at = generation, sampled_at
        return
    counter.total_up += up - counter.last_up
    counter.total_down += down - counter.last_down
    counter.last_up, counter.last_down = up, down
    counter.sampled_at = sampled_at
