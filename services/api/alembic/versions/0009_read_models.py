"""Persist read projections and composite read-path indexes."""
from alembic import op
import sqlalchemy as sa
revision = "0009_read_models"
down_revision = "0008_policy_log_action_length"
branch_labels = depends_on = None


def upgrade():
    op.create_table("read_models", sa.Column("key", sa.String(128), primary_key=True),
                    sa.Column("as_of", sa.Date(), primary_key=True),
                    sa.Column("version", sa.String(128), nullable=False),
                    sa.Column("payload", sa.JSON(), nullable=False),
                    sa.Column("updated_at", sa.DateTime(timezone=True)))
    op.create_index("ix_invoices_status_due_date", "invoices", ["status", "due_date"], if_not_exists=True)
    op.create_index("ix_payments_invoice_occurred", "payments", ["invoice_id", "occurred_at"], if_not_exists=True)


def downgrade():
    op.drop_index("ix_payments_invoice_occurred", "payments")
    op.drop_index("ix_invoices_status_due_date", "invoices")
    op.drop_table("read_models")
