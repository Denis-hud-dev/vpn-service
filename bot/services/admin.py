"""Opt-in, read-only administrative overview. No bot route exposes it yet."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from bot.db.models import NodeHealth, Payment, Subscription, User, VPNNode


@dataclass(frozen=True)
class AdminSummary:
    users: int
    active_subscriptions: int
    expired_subscriptions: int
    pending_subscriptions: int
    blocked_subscriptions: int
    successful_payments: int
    paid_kopecks: int
    enabled_nodes: int
    healthy_nodes: int


class AdminReadService:
    """Explicit identity allowlist, with authorization before any DB access.

    The caller must authenticate the Telegram actor independently; accepting
    a user-supplied actor ID from a request body would bypass this boundary.
    """

    def __init__(self, sessions, allowed_telegram_ids: frozenset[int]):
        if any(identity <= 0 for identity in allowed_telegram_ids):
            raise ValueError("Некорректный администратор")
        self.sessions = sessions
        self.allowed_telegram_ids = allowed_telegram_ids

    async def summary(self, actor_telegram_id: int) -> AdminSummary:
        if actor_telegram_id not in self.allowed_telegram_ids:
            raise PermissionError("Нет прав администратора")
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
        async with self.sessions() as session:
            user_count = await session.scalar(select(func.count()).select_from(User))
            statuses = dict((await session.execute(select(
                Subscription.status, func.count()).group_by(Subscription.status))).all())
            paid_count, paid_total = (await session.execute(select(
                func.count(), func.coalesce(func.sum(Payment.amount_kopecks), 0)
            ).where(Payment.status == 'succeeded'))).one()
            enabled_nodes = await session.scalar(select(func.count()).select_from(VPNNode).where(
                VPNNode.enabled.is_(True)))
            healthy_nodes = await session.scalar(select(func.count()).select_from(VPNNode).join(
                NodeHealth, NodeHealth.node_id == VPNNode.id).where(
                    VPNNode.enabled.is_(True), NodeHealth.available.is_(True),
                    NodeHealth.checked_at >= cutoff))
        return AdminSummary(
            users=user_count or 0,
            active_subscriptions=statuses.get('active', 0),
            expired_subscriptions=statuses.get('expired', 0),
            pending_subscriptions=statuses.get('pending', 0),
            blocked_subscriptions=statuses.get('blocked', 0),
            successful_payments=paid_count,
            paid_kopecks=paid_total,
            enabled_nodes=enabled_nodes or 0,
            healthy_nodes=healthy_nodes or 0,
        )
