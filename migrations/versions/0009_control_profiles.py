"""Offline KudaVPN Control schema; no existing VPN credentials are migrated."""
from alembic import op
import sqlalchemy as sa

revision = '0009_control_profiles'
down_revision = '0008_premium_notices'
branch_labels = depends_on = None


def upgrade():
    op.create_table('device_profiles',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('subscription_id', sa.Uuid(), sa.ForeignKey('subscriptions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('node_id', sa.Uuid(), sa.ForeignKey('vpn_nodes.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('credential', sa.Uuid(), nullable=False, unique=True),
        sa.Column('name', sa.String(64), nullable=False),
        sa.Column('platform', sa.String(16), nullable=False),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('applied_expires_at', sa.DateTime(timezone=True)),
        sa.Column('lease_revision', sa.Integer(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True)),
        sa.UniqueConstraint('node_id', name='device_profile_dedicated_node'),
        sa.CheckConstraint("platform IN ('ios','android','windows','macos')", name='device_profile_platform'),
        sa.CheckConstraint("state IN ('pending','active','revoking','revoked')", name='device_profile_state'),
        sa.CheckConstraint('length(name) > 0', name='device_profile_name'))
    op.create_index('ix_device_profiles_subscription_id', 'device_profiles', ['subscription_id'])
    op.create_index('ix_device_profiles_node_id', 'device_profiles', ['node_id'])
    op.create_table('profile_traffic',
        sa.Column('profile_id', sa.Uuid(), sa.ForeignKey('device_profiles.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('total_up', sa.BigInteger(), nullable=False),
        sa.Column('total_down', sa.BigInteger(), nullable=False),
        sa.Column('last_up', sa.BigInteger()),
        sa.Column('last_down', sa.BigInteger()),
        sa.Column('source_generation', sa.String(64)),
        sa.Column('sampled_at', sa.DateTime(timezone=True)),
        sa.Column('complete', sa.Boolean(), nullable=False),
        sa.CheckConstraint('total_up >= 0 AND total_down >= 0', name='profile_traffic_total_nonnegative'),
        sa.CheckConstraint('last_up IS NULL OR last_up >= 0', name='profile_traffic_up_nonnegative'),
        sa.CheckConstraint('last_down IS NULL OR last_down >= 0', name='profile_traffic_down_nonnegative'))


def downgrade():
    op.drop_table('profile_traffic')
    op.drop_index('ix_device_profiles_node_id', table_name='device_profiles')
    op.drop_index('ix_device_profiles_subscription_id', table_name='device_profiles')
    op.drop_table('device_profiles')
