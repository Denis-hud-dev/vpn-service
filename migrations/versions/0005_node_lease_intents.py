"""Persist desired node operation before making a remote call."""
from alembic import op
import sqlalchemy as sa

revision = '0005_node_lease_intents'
down_revision = '0004_happ_subscriptions'
branch_labels = depends_on = None


def upgrade():
    op.add_column('node_access', sa.Column('desired_enabled', sa.Boolean(), nullable=False,
                                           server_default=sa.false()))
    op.add_column('node_access', sa.Column('desired_expires_at', sa.DateTime(timezone=True)))
    op.add_column('node_access', sa.Column('lease_revision', sa.Integer(), nullable=False,
                                           server_default='0'))


def downgrade():
    op.drop_column('node_access', 'lease_revision')
    op.drop_column('node_access', 'desired_expires_at')
    op.drop_column('node_access', 'desired_enabled')
