"""Track isolated synthetic demo snapshots by browser session."""
from alembic import op
import sqlalchemy as sa

revision = "0017_isolated_demo_sessions"
down_revision = "0016_review_workflows"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "demo_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source", sa.String(length=16), nullable=False, unique=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_demo_sessions_source", "demo_sessions", ["source"])


def downgrade():
    op.drop_index("ix_demo_sessions_source", table_name="demo_sessions")
    op.drop_table("demo_sessions")
