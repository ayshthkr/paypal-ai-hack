"""Add review states for reply triage and collections work."""
from alembic import op
import sqlalchemy as sa

revision = "0016_review_workflows"
down_revision = "0015_sandbox_invoice_idempotency"
branch_labels = depends_on = None


def upgrade():
    op.add_column("reply_triages", sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"))
    op.add_column("reply_triages", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_reply_triages_state", "reply_triages", ["state"])
    op.create_table(
        "collection_reviews",
        sa.Column("invoice_id", sa.String(length=36), sa.ForeignKey("invoices.id"), primary_key=True),
        sa.Column("owner", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("follow_up_date", sa.Date(), nullable=True),
        sa.Column("resolution_status", sa.String(length=24), nullable=False, server_default="open"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_collection_reviews_resolution_status", "collection_reviews", ["resolution_status"])


def downgrade():
    op.drop_index("ix_collection_reviews_resolution_status", table_name="collection_reviews")
    op.drop_table("collection_reviews")
    op.drop_index("ix_reply_triages_state", table_name="reply_triages")
    op.drop_column("reply_triages", "reviewed_at")
    op.drop_column("reply_triages", "state")
