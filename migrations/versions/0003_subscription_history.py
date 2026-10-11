"""Append subscription audit history without changing existing records."""
from alembic import op
import sqlalchemy as sa
revision = '0003_subscription_history'
down_revision = '0002_active_screens'
branch_labels = depends_on = None


def upgrade():
    op.create_table('subscription_history',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('subscription_id', sa.Uuid(), sa.ForeignKey('subscriptions.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('payment_id', sa.Uuid(), sa.ForeignKey('payments.id', ondelete='RESTRICT'), unique=True),
        sa.Column('reason', sa.String(32), nullable=False),
        sa.Column('old_status', sa.String(16)),
        sa.Column('new_status', sa.String(16), nullable=False),
        *[sa.Column(name, sa.DateTime(timezone=True)) for name in
          ('old_expires_at', 'new_expires_at', 'old_starts_at', 'new_starts_at')])
    op.create_index('ix_subscription_history_subscription_id', 'subscription_history', ['subscription_id'])


def downgrade():
    op.drop_table('subscription_history')
