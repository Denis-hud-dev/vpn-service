"""Pilot notification delivery ledger; no payment or subscription secrets."""
from alembic import op
import sqlalchemy as sa

revision = '0008_premium_notices'
down_revision = '0007_happ_link_nonce'
branch_labels = depends_on = None


def upgrade():
    op.create_table('premium_notices',
        sa.Column('event_key', sa.String(length=128), primary_key=True),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('claimed_until', sa.DateTime(timezone=True)),
        sa.Column('sent_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('telegram_id > 0', name='premium_notice_positive_user'),
        sa.CheckConstraint("kind IN ('payment','expiry')", name='premium_notice_kind'))


def downgrade():
    op.drop_table('premium_notices')
