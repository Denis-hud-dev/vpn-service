"""Track recent control-plane health without changing existing VPN nodes."""
from alembic import op
import sqlalchemy as sa

revision = '0006_node_health'
down_revision = '0005_node_lease_intents'
branch_labels = depends_on = None


def upgrade():
    op.create_table('node_health',
        sa.Column('node_id', sa.Uuid(), sa.ForeignKey('vpn_nodes.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('available', sa.Boolean(), nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('config_digest', sa.String(length=64), nullable=False))


def downgrade():
    op.drop_table('node_health')
