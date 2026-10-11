"""Add token hashes, public node metadata and per-node credentials."""
from alembic import op
import sqlalchemy as sa
revision = '0004_happ_subscriptions'
down_revision = '0003_subscription_history'
branch_labels = depends_on = None


def record():
    return [sa.Column('id',sa.Uuid(),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False)]


def upgrade():
    op.create_table('subscription_tokens',*record(),
        sa.Column('subscription_id',sa.Uuid(),sa.ForeignKey('subscriptions.id',ondelete='RESTRICT'),unique=True,nullable=False),
        sa.Column('digest',sa.String(64),unique=True,nullable=False),
        sa.Column('revoked',sa.Boolean(),nullable=False))
    op.create_table('vpn_nodes',*record(),
        sa.Column('name',sa.String(128),unique=True,nullable=False),
        sa.Column('hostname',sa.String(253),nullable=False),
        sa.Column('port',sa.Integer(),nullable=False),
        sa.Column('public_key',sa.String(128),nullable=False),
        sa.Column('short_id',sa.String(16),nullable=False),
        sa.Column('server_name',sa.String(253),nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.CheckConstraint('port BETWEEN 1 AND 65535',name='vpn_node_port'))
    op.create_table('node_access',*record(),
        sa.Column('subscription_id',sa.Uuid(),sa.ForeignKey('subscriptions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('node_id',sa.Uuid(),sa.ForeignKey('vpn_nodes.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('credential',sa.Uuid(),unique=True,nullable=False),
        sa.Column('enabled',sa.Boolean(),nullable=False),
        sa.Column('revocation_confirmed',sa.Boolean(),nullable=False),
        sa.Column('applied_expires_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('subscription_id','node_id',name='node_access_owner'))
    for col in ['subscription_id','node_id']:
        op.create_index('ix_node_access_'+col,'node_access',[col])


def downgrade():
    for table in ['node_access','vpn_nodes','subscription_tokens']:op.drop_table(table)
