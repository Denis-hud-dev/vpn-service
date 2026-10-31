"""Sandbox receipt jobs, manual refund holds and owner alert outbox. Additive."""
from alembic import op
import sqlalchemy as sa

revision='0010_npd_queue'
down_revision='0009_website'
branch_labels=depends_on=None


def upgrade():
    op.create_table('npd_receipt_jobs',
        sa.Column('payment_id',sa.Uuid(),sa.ForeignKey('npd_receipts.payment_id',ondelete='RESTRICT'),primary_key=True),
        sa.Column('operation_key',sa.String(64),nullable=False,unique=True),
        sa.Column('state',sa.String(24),nullable=False),
        sa.Column('attempts',sa.Integer(),nullable=False),
        sa.Column('due_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('lease_token',sa.String(32)),sa.Column('lease_until',sa.DateTime(timezone=True)),
        sa.Column('error_code',sa.String(32)),sa.Column('document_ref',sa.String(128)),
        sa.Column('receipt_url',sa.String(512)),
        sa.CheckConstraint("state IN ('queued','processing','retry','simulated','review')",name='npd_job_state'),
        sa.CheckConstraint('attempts >= 0',name='npd_job_attempts'),
        sa.CheckConstraint("state != 'processing' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)",name='npd_job_lease'))
    op.create_index('ix_npd_receipt_jobs_due_at','npd_receipt_jobs',['due_at'])
    op.create_table('npd_receipt_holds',
        sa.Column('refund_id',sa.String(64),primary_key=True),
        sa.Column('payment_id',sa.Uuid(),sa.ForeignKey('payments.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('amount_kopecks',sa.Integer(),nullable=False),sa.Column('state',sa.String(16),nullable=False),
        sa.CheckConstraint('amount_kopecks > 0',name='npd_hold_amount'),
        sa.CheckConstraint("state = 'review'",name='npd_hold_manual_only'))
    op.create_index('ix_npd_receipt_holds_payment_id','npd_receipt_holds',['payment_id'])
    op.create_table('npd_admin_alerts',
        sa.Column('event_key',sa.String(128),primary_key=True),
        sa.Column('payment_id',sa.Uuid(),sa.ForeignKey('payments.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('kind',sa.String(24),nullable=False),sa.Column('state',sa.String(16),nullable=False),
        sa.Column('lease_token',sa.String(32)),sa.Column('lease_until',sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('retry','review','recovered','refund_review')",name='npd_alert_kind'),
        sa.CheckConstraint("state IN ('pending','sending','sent')",name='npd_alert_state'),
        sa.CheckConstraint("state != 'sending' OR (lease_token IS NOT NULL AND lease_until IS NOT NULL)",name='npd_alert_lease'))
    op.create_index('ix_npd_admin_alerts_payment_id','npd_admin_alerts',['payment_id'])


def downgrade():
    for table in ('npd_admin_alerts','npd_receipt_holds','npd_receipt_jobs'):op.drop_table(table)
