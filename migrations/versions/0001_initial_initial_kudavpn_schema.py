"""Initial KudaVPN schema"""
from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
    sa.Column('telegram_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.String(length=32), nullable=True),
    sa.Column('first_name', sa.String(length=256), nullable=False),
    sa.Column('last_name', sa.String(length=256), nullable=True),
    sa.Column('referrer_id', sa.BigInteger(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('referrer_id IS NULL OR (referrer_id > 0 AND referrer_id != telegram_id)', name='user_valid_referrer'),
    sa.CheckConstraint('telegram_id > 0', name='user_positive_telegram_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('telegram_id')
    )
    op.create_table('vpn_servers',
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('hostname', sa.String(length=253), nullable=False),
    sa.Column('port', sa.Integer(), nullable=False),
    sa.Column('location', sa.String(length=128), nullable=False),
    sa.Column('public_key', sa.String(length=128), nullable=True),
    sa.Column('subnet', sa.String(length=64), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('port BETWEEN 1 AND 65535', name='vpn_server_port'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('subscriptions',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('tariff_id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('device_limit', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("status != 'active' OR (starts_at IS NOT NULL AND expires_at IS NOT NULL)", name='subscription_active_dates'),
    sa.CheckConstraint("status IN ('active','expired','pending','blocked')", name='subscription_status'),
    sa.CheckConstraint('device_limit > 0', name='subscription_device_limit'),
    sa.CheckConstraint('expires_at IS NULL OR starts_at IS NULL OR expires_at > starts_at', name='subscription_dates'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'user_id', name='subscription_owner'),
    sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_subscriptions_expires_at'), 'subscriptions', ['expires_at'], unique=False)
    op.create_table('devices',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('subscription_id', sa.Uuid(), nullable=False),
    sa.Column('vpn_server_id', sa.Uuid(), nullable=True),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('platform', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('public_key', sa.String(length=128), nullable=True),
    sa.Column('assigned_ip', sa.String(length=45), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("platform IN ('ios','android','windows','macos')", name='device_platform'),
    sa.CheckConstraint("status != 'active' OR (vpn_server_id IS NOT NULL AND public_key IS NOT NULL AND assigned_ip IS NOT NULL)", name='device_active_configuration'),
    sa.CheckConstraint("status IN ('pending','active','revoked')", name='device_status'),
    sa.ForeignKeyConstraint(['subscription_id', 'user_id'], ['subscriptions.id', 'subscriptions.user_id'], name='device_subscription_owner', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['vpn_server_id'], ['vpn_servers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('public_key'),
    sa.UniqueConstraint('vpn_server_id', 'assigned_ip', name='device_server_ip')
    )
    op.create_index(op.f('ix_devices_subscription_id'), 'devices', ['subscription_id'], unique=False)
    op.create_index(op.f('ix_devices_user_id'), 'devices', ['user_id'], unique=False)
    op.create_index(op.f('ix_devices_vpn_server_id'), 'devices', ['vpn_server_id'], unique=False)
    op.create_table('payments',
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('subscription_id', sa.Uuid(), nullable=True),
    sa.Column('tariff_id', sa.String(length=32), nullable=False),
    sa.Column('amount_kopecks', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('provider_payment_id', sa.String(length=128), nullable=True),
    sa.Column('idempotency_key', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("currency = 'RUB'", name='payment_currency'),
    sa.CheckConstraint("status IN ('pending','succeeded','canceled','refunded')", name='payment_status'),
    sa.CheckConstraint('amount_kopecks > 0', name='payment_positive_amount'),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key'),
    sa.UniqueConstraint('provider', 'provider_payment_id', name='payment_provider_id')
    )
    op.create_index(op.f('ix_payments_subscription_id'), 'payments', ['subscription_id'], unique=False)
    op.create_index(op.f('ix_payments_user_id'), 'payments', ['user_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_payments_user_id'), table_name='payments')
    op.drop_index(op.f('ix_payments_subscription_id'), table_name='payments')
    op.drop_table('payments')
    op.drop_index(op.f('ix_devices_vpn_server_id'), table_name='devices')
    op.drop_index(op.f('ix_devices_user_id'), table_name='devices')
    op.drop_index(op.f('ix_devices_subscription_id'), table_name='devices')
    op.drop_table('devices')
    op.drop_index(op.f('ix_subscriptions_expires_at'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_table('vpn_servers')
    op.drop_table('users')
