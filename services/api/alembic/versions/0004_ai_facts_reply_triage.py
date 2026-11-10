"""Add grounded AI draft audit fields and reply triage history."""

from alembic import op
import sqlalchemy as sa


revision = "0004_ai_facts_reply_triage"
down_revision = "0003_drift_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("promised_payment_date", sa.Date(), nullable=True))
    op.add_column("recommendations", sa.Column("draft_source", sa.String(length=24), nullable=False, server_default="template"))
    op.add_column("recommendations", sa.Column("drafted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table(
        "reply_triages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("invoice_id", sa.String(length=36), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("classification", sa.String(length=24), nullable=False),
        sa.Column("promised_payment_date", sa.Date(), nullable=True),
        sa.Column("classifier", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_reply_triages_invoice_id", "reply_triages", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_reply_triages_invoice_id", table_name="reply_triages")
    op.drop_table("reply_triages")
    op.drop_column("recommendations", "drafted_at")
    op.drop_column("recommendations", "draft_source")
    op.drop_column("invoices", "promised_payment_date")
