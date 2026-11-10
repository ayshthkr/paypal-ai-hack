"""Persist operator sessions so logout revokes them server side."""
from alembic import op
import sqlalchemy as sa

revision = "0014_operator_sessions"
down_revision = "0013_policy_date"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "operator_sessions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("issued_at", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.Integer(), nullable=True),
    )
    op.create_index("ix_operator_sessions_expires_at", "operator_sessions", ["expires_at"])


def downgrade():
    op.drop_index("ix_operator_sessions_expires_at", table_name="operator_sessions")
    op.drop_table("operator_sessions")
