"""Create the seven V1 domain tables.

Revision ID: 0001_initial
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("clients",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("provider_customer_id", sa.String(255), unique=True),
        sa.Column("display_name", sa.String(255), nullable=False), sa.Column("email", sa.String(320)),
        sa.Column("source", sa.String(16), nullable=False), sa.Column("drift", sa.JSON(), nullable=False),
        sa.Column("scored_at", sa.DateTime(timezone=True)))
    op.create_index("ix_clients_source", "clients", ["source"])
    op.create_table("invoices",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("paypal_id", sa.String(255), unique=True),
        sa.Column("invoice_number", sa.String(100), nullable=False), sa.Column("client_id", sa.String(36), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False), sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("total", sa.Numeric(14, 2), nullable=False), sa.Column("paid", sa.Numeric(14, 2), nullable=False),
        sa.Column("outstanding", sa.Numeric(14, 2), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False), sa.Column("disputed", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False), sa.Column("provider_payload", sa.JSON(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True)), sa.Column("material_version", sa.Integer(), nullable=False))
    for col in ("client_id", "status", "source"):
        op.create_index(f"ix_invoices_{col}", "invoices", [col])
    op.create_table("payments",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("provider_payment_id", sa.String(255), nullable=False, unique=True),
        sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False), sa.Column("source", sa.String(16), nullable=False))
    op.create_index("ix_payments_invoice_id", "payments", ["invoice_id"])
    op.create_table("invoice_events",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("provider_event_id", sa.String(255), nullable=False, unique=True),
        sa.Column("event_type", sa.String(128), nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True)),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False), sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False), sa.Column("processing_status", sa.String(24), nullable=False),
        sa.Column("failure_reason", sa.Text()))
    op.create_table("predictions",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("scoring_date", sa.Date(), nullable=False), sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False), sa.Column("feature_schema_version", sa.String(64), nullable=False),
        sa.Column("curve", sa.JSON(), nullable=False), sa.Column("quantiles", sa.JSON(), nullable=False),
        sa.Column("probabilities", sa.JSON(), nullable=False), sa.Column("reasons", sa.JSON(), nullable=False),
        sa.UniqueConstraint("invoice_id", "scoring_date", "fingerprint", "model_version"))
    op.create_index("ix_predictions_invoice_id", "predictions", ["invoice_id"])
    op.create_table("recommendations",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("invoice_id", sa.String(36), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("action", sa.String(16), nullable=False), sa.Column("tone", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column("edited_text", sa.Text(), nullable=False), sa.Column("invoice_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False), sa.Column("approved_hash", sa.String(64)),
        sa.Column("approved_text", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)))
    for col in ("invoice_id", "state"):
        op.create_index(f"ix_recommendations_{col}", "recommendations", [col])
    op.create_table("action_attempts",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("recommendation_id", sa.String(36), sa.ForeignKey("recommendations.id"), nullable=False),
        sa.Column("attempt_key", sa.String(64), nullable=False, unique=True), sa.Column("state", sa.String(16), nullable=False),
        sa.Column("provider_reference", sa.String(255)), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)), sa.Column("error", sa.Text()))
    op.create_index("ix_action_attempts_recommendation_id", "action_attempts", ["recommendation_id"])


def downgrade() -> None:
    op.drop_table("action_attempts")
    op.drop_table("recommendations")
    op.drop_table("predictions")
    op.drop_table("invoice_events")
    op.drop_table("payments")
    op.drop_table("invoices")
    op.drop_table("clients")
