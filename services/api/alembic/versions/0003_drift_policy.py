"""Persist reminder ladder steps and policy decision audit logs."""

from alembic import op
import sqlalchemy as sa


revision = "0003_drift_policy"
down_revision = "0002_demo_timeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("reminder_step", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("recommendations", sa.Column("policy_step", sa.Integer(), nullable=True))
    op.create_table(
        "policy_decision_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("invoice_id", sa.String(length=36), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("decision_date", sa.Date(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("features", sa.JSON(), nullable=False),
        sa.Column("chosen_action", sa.String(length=32), nullable=False),
        sa.Column("alternatives", sa.JSON(), nullable=False),
        sa.Column("assigned_holdback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("outcome", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index("ix_policy_decision_logs_invoice_id", "policy_decision_logs", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_policy_decision_logs_invoice_id", table_name="policy_decision_logs")
    op.drop_table("policy_decision_logs")
    op.drop_column("recommendations", "policy_step")
    op.drop_column("invoices", "reminder_step")
