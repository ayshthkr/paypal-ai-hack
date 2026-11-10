"""Persist demo clock and invoice timeline fields."""

from alembic import op
import sqlalchemy as sa


revision = "0002_demo_timeline"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_state",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("seeded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column("invoices", sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("partial_payment_allowed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("invoices", sa.Column("payment_term_type", sa.String(length=32), nullable=True))
    op.add_column("invoices", sa.Column("reminder_events", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.create_index("ix_invoices_status_due_date", "invoices", ["status", "due_date"])


def downgrade() -> None:
    op.drop_index("ix_invoices_status_due_date", table_name="invoices")
    op.drop_column("invoices", "reminder_events")
    op.drop_column("invoices", "payment_term_type")
    op.drop_column("invoices", "partial_payment_allowed")
    op.drop_column("invoices", "viewed_at")
    op.drop_table("demo_state")
