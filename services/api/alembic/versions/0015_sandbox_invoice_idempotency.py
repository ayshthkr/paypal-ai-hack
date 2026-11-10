"""Persist Sandbox invoice creation keys across retries."""
from alembic import op
import sqlalchemy as sa

revision = "0015_sandbox_invoice_idempotency"
down_revision = "0014_operator_sessions"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "sandbox_invoice_operations",
        sa.Column("idempotency_key", sa.String(length=64), primary_key=True),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("invoice_number", sa.String(length=100), nullable=False, unique=True),
        sa.Column("paypal_invoice_id", sa.String(length=255), nullable=True),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="reserved"),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sandbox_invoice_operations_state", "sandbox_invoice_operations", ["state"])


def downgrade():
    op.drop_index("ix_sandbox_invoice_operations_state", table_name="sandbox_invoice_operations")
    op.drop_table("sandbox_invoice_operations")
