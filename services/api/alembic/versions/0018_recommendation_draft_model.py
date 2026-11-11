"""Persist the model used to generate each reminder draft."""
from alembic import op
import sqlalchemy as sa


revision = "0018_recommendation_draft_model"
down_revision = "0017_isolated_demo_sessions"
branch_labels = depends_on = None


def upgrade():
    op.add_column("recommendations", sa.Column("draft_model", sa.String(length=96), nullable=True))


def downgrade():
    op.drop_column("recommendations", "draft_model")
