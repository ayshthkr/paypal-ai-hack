"""Widen policy decision log identifiers and fingerprints."""

from alembic import op
import sqlalchemy as sa


revision = "0007_policy_log_column_lengths"
down_revision = "0006_demo_reset_rate_limit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("policy_decision_logs") as batch_op:
        batch_op.alter_column(
            "id",
            existing_type=sa.String(length=32),
            type_=sa.String(length=36),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "fingerprint",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Widening these columns is backward-compatible; shrinking them could truncate
    # UUIDs or invalidate SHA-256 fingerprints written after this migration.
    pass
