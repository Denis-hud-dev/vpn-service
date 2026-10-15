"""Allow stable link delivery without storing bearer tokens in plaintext."""
from alembic import op
import sqlalchemy as sa

revision = '0007_happ_link_nonce'
down_revision = '0006_node_health'
branch_labels = depends_on = None


def upgrade():
    op.add_column('subscription_tokens', sa.Column('derivation_nonce', sa.String(length=32), nullable=True))


def downgrade():
    op.drop_column('subscription_tokens', 'derivation_nonce')
