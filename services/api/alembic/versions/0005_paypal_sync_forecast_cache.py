"""Persist PayPal sync state, recipient views, and reusable forecasts."""

from alembic import op
import sqlalchemy as sa


revision = "0005_paypal_sync_forecast_cache"
down_revision = "0004_ai_facts_reply_triage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("viewed_by_recipient", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("invoices", sa.Column("viewed_observed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("predictions", sa.Column("model_label", sa.String(length=64), nullable=False, server_default="kaplan_meier"))
    op.add_column("predictions", sa.Column("history_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("predictions", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    with op.batch_alter_table("recommendations") as batch:
        batch.alter_column("action", existing_type=sa.String(length=16), type_=sa.String(length=32), existing_nullable=False)
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("created_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_refreshes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_sync_runs_status", "sync_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sync_runs_status", table_name="sync_runs")
    op.drop_table("sync_runs")
    with op.batch_alter_table("recommendations") as batch:
        batch.alter_column("action", existing_type=sa.String(length=32), type_=sa.String(length=16), existing_nullable=False)
    op.drop_column("predictions", "scored_at")
    op.drop_column("predictions", "history_count")
    op.drop_column("predictions", "model_label")
    op.drop_column("invoices", "viewed_observed_at")
    op.drop_column("invoices", "viewed_by_recipient")
