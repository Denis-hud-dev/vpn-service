"""No private keys or payment credentials belong in these tables."""
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    PENDING = "pending"
    BLOCKED = "blocked"


class Base(DeclarativeBase):
    pass


class Record:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Record, Base):
    __tablename__ = "users"
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(32))
    first_name: Mapped[str] = mapped_column(String(256))
    last_name: Mapped[str | None] = mapped_column(String(256))
    # Telegram ID: attribution can precede the referrer's own registration.
    referrer_id: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (CheckConstraint("telegram_id > 0", name="user_positive_telegram_id"),
                     CheckConstraint("referrer_id IS NULL OR (referrer_id > 0 AND referrer_id != telegram_id)", name="user_valid_referrer"))


class WebIdentity(Base):
    __tablename__ = 'web_identities'
    subject: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), unique=True)


class WebLoginFlow(Base):
    __tablename__ = 'web_login_flows'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_digest: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class WebSession(Base):
    __tablename__ = 'web_sessions'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReviewSession(Base):
    """Review auth only; no copied canonical User/Subscription records."""
    __tablename__ = 'review_sessions'
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (CheckConstraint('owner_id > 0', name='review_session_owner'),)


class NPDReceipt(Base):
    """Obligation ledger, never a fabricated fiscal receipt."""
    __tablename__ = 'npd_receipts'
    payment_id: Mapped[UUID] = mapped_column(ForeignKey('payments.id', ondelete='RESTRICT'), primary_key=True)
    environment: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(24))
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    # Opaque private document reference; never returned as a public URL.
    document_ref: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        CheckConstraint("environment IN ('test','live')", name='npd_environment'),
        CheckConstraint("state IN ('test_not_required','pending','issued','delivered','error','void_pending','voided')", name='npd_state'),
        CheckConstraint('amount_kopecks > 0', name='npd_amount'),
        CheckConstraint("environment != 'test' OR state = 'test_not_required'", name='npd_no_test_receipt'),
        CheckConstraint("state NOT IN ('issued','delivered') OR document_ref IS NOT NULL", name='npd_issued_document'),
    )


class NPDReceiptEvent(Base):
    __tablename__ = 'npd_receipt_events'
    operation: Mapped[str] = mapped_column(String(128), primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(ForeignKey('npd_receipts.payment_id', ondelete='RESTRICT'), index=True)
    state: Mapped[str] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(32))
    document_ref: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NPDReceiptJob(Base):
    """Sandbox dispatcher state, one job per existing receipt obligation."""
    __tablename__ = 'npd_receipt_jobs'
    payment_id: Mapped[UUID] = mapped_column(ForeignKey('npd_receipts.payment_id', ondelete='RESTRICT'), primary_key=True)
    operation_key: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(24), default='queued')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(32))
    document_ref: Mapped[str | None] = mapped_column(String(128))
    # Private simulation URL. Never put it in logs or administrator messages.
    receipt_url: Mapped[str | None] = mapped_column(String(512))
    __table_args__ = (
        CheckConstraint("state IN ('queued','processing','retry','simulated','review')", name='npd_job_state'),
        CheckConstraint('attempts >= 0', name='npd_job_attempts'),
        CheckConstraint("state != 'processing' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)", name='npd_job_lease'),
    )


class NPDReceiptHold(Base):
    """Verified refund intent can precede receipt creation; review is manual."""
    __tablename__ = 'npd_receipt_holds'
    refund_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(ForeignKey('payments.id', ondelete='RESTRICT'), index=True)
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default='review')
    __table_args__ = (
        CheckConstraint('amount_kopecks > 0', name='npd_hold_amount'),
        CheckConstraint("state = 'review'", name='npd_hold_manual_only'),
    )


class NPDAdminAlert(Base):
    """Owner-only durable outbox; no payer contacts or receipt URLs."""
    __tablename__ = 'npd_admin_alerts'
    event_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    payment_id: Mapped[UUID] = mapped_column(ForeignKey('payments.id', ondelete='RESTRICT'), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(16), default='pending')
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("kind IN ('retry','review','recovered','refund_review')", name='npd_alert_kind'),
        CheckConstraint("state IN ('pending','sending','sent')", name='npd_alert_state'),
        CheckConstraint("state != 'sending' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)", name='npd_alert_lease'),
    )


class Subscription(Record, Base):
    __tablename__ = "subscriptions"
    # One current subscription per user; payment rows retain renewal history.
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True)
    tariff_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default=SubscriptionStatus.PENDING)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    device_limit: Mapped[int] = mapped_column(Integer, default=5)
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="subscription_owner"),
        CheckConstraint("status IN ('active','expired','pending','blocked')", name="subscription_status"),
        CheckConstraint("device_limit > 0", name="subscription_device_limit"),
        CheckConstraint("expires_at IS NULL OR starts_at IS NULL OR expires_at > starts_at", name="subscription_dates"),
        CheckConstraint("status != 'active' OR (starts_at IS NOT NULL AND expires_at IS NOT NULL)", name="subscription_active_dates"),
    )


class VPNServer(Record, Base):
    __tablename__ = "vpn_servers"
    name: Mapped[str] = mapped_column(String(128), unique=True)
    hostname: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=51820)
    location: Mapped[str] = mapped_column(String(128))
    public_key: Mapped[str | None] = mapped_column(String(128))
    subnet: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (CheckConstraint("port BETWEEN 1 AND 65535", name="vpn_server_port"),)


class Device(Record, Base):
    __tablename__ = "devices"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    subscription_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    vpn_server_id: Mapped[UUID | None] = mapped_column(ForeignKey("vpn_servers.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    platform: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    public_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    assigned_ip: Mapped[str | None] = mapped_column(String(45))
    __table_args__ = (
        ForeignKeyConstraint(["subscription_id", "user_id"], ["subscriptions.id", "subscriptions.user_id"], ondelete="RESTRICT", name="device_subscription_owner"),
        CheckConstraint("status != 'active' OR (vpn_server_id IS NOT NULL AND public_key IS NOT NULL AND assigned_ip IS NOT NULL)", name="device_active_configuration"),
        CheckConstraint("platform IN ('ios','android','windows','macos')", name="device_platform"),
        CheckConstraint("status IN ('pending','active','revoked')", name="device_status"),
        UniqueConstraint("vpn_server_id", "assigned_ip", name="device_server_ip"),
    )


class Payment(Record, Base):
    __tablename__ = "payments"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    subscription_id: Mapped[UUID | None] = mapped_column(ForeignKey("subscriptions.id", ondelete="RESTRICT"), index=True)
    tariff_id: Mapped[str] = mapped_column(String(32))
    amount_kopecks: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    provider: Mapped[str] = mapped_column(String(32), default="yookassa")
    provider_payment_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[UUID] = mapped_column(Uuid, unique=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="payment_provider_id"),
        CheckConstraint("amount_kopecks > 0", name="payment_positive_amount"),
        CheckConstraint("currency = 'RUB'", name="payment_currency"),
        CheckConstraint("status IN ('pending','succeeded','canceled','refunded')", name="payment_status"),
    )


class PremiumNotice(Base):
    """Delivery ledger for pilot-only Telegram notices; contains no links or secrets."""
    __tablename__ = "premium_notices"
    event_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("telegram_id > 0", name="premium_notice_positive_user"),
                     CheckConstraint("kind IN ('payment','expiry')", name="premium_notice_kind"))


class ActiveScreen(Base):
    __tablename__ = "active_screens"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.telegram_id", ondelete="RESTRICT"), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("chat_id", "message_id", name="active_screen_message"),
        CheckConstraint("chat_id = user_id AND chat_id > 0", name="active_screen_private_chat"),
        CheckConstraint("message_id > 0", name="active_screen_message_id"),
    )


class SubscriptionHistory(Record, Base):
    __tablename__ = "subscription_history"
    subscription_id: Mapped[UUID] = mapped_column(ForeignKey("subscriptions.id", ondelete="RESTRICT"), index=True)
    payment_id: Mapped[UUID | None] = mapped_column(ForeignKey("payments.id", ondelete="RESTRICT"), unique=True)
    reason: Mapped[str] = mapped_column(String(32))
    old_status: Mapped[str | None] = mapped_column(String(16))
    new_status: Mapped[str] = mapped_column(String(16))
    old_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    old_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    new_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionToken(Record, Base):
    __tablename__ = "subscription_tokens"
    subscription_id: Mapped[UUID] = mapped_column(ForeignKey("subscriptions.id", ondelete="RESTRICT"), unique=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    # A random nonce, not the bearer token. With a separate HMAC key the bot can
    # show the same link again without storing its plaintext in PostgreSQL.
    derivation_nonce: Mapped[str | None] = mapped_column(String(32))


class VPNNode(Record, Base):
    """Public REALITY parameters only; private keys and admin credentials stay on nodes."""
    __tablename__ = "vpn_nodes"
    name: Mapped[str] = mapped_column(String(128), unique=True)
    hostname: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=443)
    public_key: Mapped[str] = mapped_column(String(128))
    short_id: Mapped[str] = mapped_column(String(16))
    server_name: Mapped[str] = mapped_column(String(253))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (CheckConstraint("port BETWEEN 1 AND 65535", name="vpn_node_port"),)


class NodeHealth(Base):
    """Last control-plane check. Missing or stale checks never enter the Happ feed."""
    __tablename__ = "node_health"
    node_id: Mapped[UUID] = mapped_column(ForeignKey("vpn_nodes.id", ondelete="CASCADE"), primary_key=True)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(64), nullable=False)


class NodeAccess(Record, Base):
    __tablename__ = "node_access"
    subscription_id: Mapped[UUID] = mapped_column(ForeignKey("subscriptions.id", ondelete="RESTRICT"), index=True)
    node_id: Mapped[UUID] = mapped_column(ForeignKey("vpn_nodes.id", ondelete="RESTRICT"), index=True)
    credential: Mapped[UUID] = mapped_column(Uuid, default=uuid4, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    revocation_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    applied_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    desired_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    desired_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_revision: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint("subscription_id", "node_id", name="node_access_owner"),)
