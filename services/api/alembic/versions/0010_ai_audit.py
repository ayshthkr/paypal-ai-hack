"""Record the grounded facts used for every generated draft and answer."""
from alembic import op
import sqlalchemy as sa
revision = "0010_ai_audit"
down_revision = "0009_read_models"
branch_labels = depends_on = None


def upgrade():
    op.create_table("ai_audits", sa.Column("id",sa.String(36),primary_key=True),
                    sa.Column("kind",sa.String(32),nullable=False),
                    sa.Column("invoice_id",sa.String(36)), sa.Column("facts",sa.JSON(),nullable=False),
                    sa.Column("output",sa.JSON(),nullable=False), sa.Column("created_at",sa.DateTime(timezone=True)))
    op.create_index("ix_ai_audits_invoice_id","ai_audits",["invoice_id"])


def downgrade():
    op.drop_table("ai_audits")
