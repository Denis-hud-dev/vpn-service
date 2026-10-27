"""Website identities, revocable sessions and NPD obligation ledger. Additive only."""
from alembic import op
import sqlalchemy as sa

revision = '0009_website'
down_revision = '0008_premium_notices'
branch_labels = depends_on = None


def upgrade():
    op.create_table('web_identities',
        sa.Column('subject', sa.String(128), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False, unique=True))
    op.create_table('web_login_flows',
        sa.Column('digest', sa.String(64), primary_key=True),
        sa.Column('browser_digest', sa.String(64), nullable=False),
        sa.Column('verifier', sa.String(128), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_web_login_flows_expires_at', 'web_login_flows', ['expires_at'])
    op.create_table('web_sessions',
        sa.Column('digest', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_web_sessions_user_id', 'web_sessions', ['user_id'])
    op.create_index('ix_web_sessions_expires_at', 'web_sessions', ['expires_at'])
    op.create_table('review_sessions',
        sa.Column('digest', sa.String(64), primary_key=True),
        sa.Column('owner_id', sa.BigInteger(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('owner_id > 0', name='review_session_owner'))
    op.create_index('ix_review_sessions_expires_at', 'review_sessions', ['expires_at'])
    op.create_table('npd_receipts',
        sa.Column('payment_id', sa.Uuid(), sa.ForeignKey('payments.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('environment', sa.String(8), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('amount_kopecks', sa.Integer(), nullable=False),
        sa.Column('document_ref', sa.String(128)),
        sa.Column('error_code', sa.String(32)),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("environment IN ('test','live')", name='npd_environment'),
        sa.CheckConstraint("state IN ('test_not_required','pending','issued','delivered','error','void_pending','voided')", name='npd_state'),
        sa.CheckConstraint('amount_kopecks > 0', name='npd_amount'),
        sa.CheckConstraint("environment != 'test' OR state = 'test_not_required'", name='npd_no_test_receipt'),
        sa.CheckConstraint("state NOT IN ('issued','delivered') OR document_ref IS NOT NULL", name='npd_issued_document'))
    op.create_table('npd_receipt_events',
        sa.Column('operation', sa.String(128), primary_key=True),
        sa.Column('payment_id', sa.Uuid(), sa.ForeignKey('npd_receipts.payment_id', ondelete='RESTRICT'), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('error_code', sa.String(32)),
        sa.Column('document_ref', sa.String(128)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index('ix_npd_receipt_events_payment_id', 'npd_receipt_events', ['payment_id'])


def downgrade():
    for table in ('npd_receipt_events', 'npd_receipts', 'review_sessions', 'web_sessions', 'web_login_flows', 'web_identities'):
        op.drop_table(table)
