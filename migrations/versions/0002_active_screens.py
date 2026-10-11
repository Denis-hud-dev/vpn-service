"""Persist the active navigation screen; existing business tables are unchanged."""
from alembic import op
import sqlalchemy as sa

revision = '0002_active_screens'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('active_screens',
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('message_id', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('user_id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.telegram_id'], ondelete='RESTRICT'),
        sa.UniqueConstraint('chat_id', 'message_id', name='active_screen_message'),
        sa.CheckConstraint('chat_id = user_id AND chat_id > 0', name='active_screen_private_chat'),
        sa.CheckConstraint('message_id > 0', name='active_screen_message_id'),
    )


def downgrade():
    op.drop_table('active_screens')
