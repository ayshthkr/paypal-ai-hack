"""Make concurrent read-driven proposal creation idempotent."""
from alembic import op
import sqlalchemy as sa
revision = "0012_unique_proposals"
down_revision = "0011_event_invoice_index"
branch_labels = depends_on = None


def upgrade():
    op.execute("""UPDATE recommendations SET state='superseded' WHERE id IN (
        SELECT id FROM (SELECT id, ROW_NUMBER() OVER (
        PARTITION BY invoice_id, invoice_version ORDER BY created_at DESC, id DESC) AS position
        FROM recommendations WHERE state='proposed') ranked WHERE position > 1)""")
    op.create_index("uq_proposed_recommendation_version", "recommendations", ["invoice_id","invoice_version"],
                    unique=True, postgresql_where=sa.text("state = 'proposed'"), sqlite_where=sa.text("state = 'proposed'"))


def downgrade():
    op.drop_index("uq_proposed_recommendation_version", "recommendations")
