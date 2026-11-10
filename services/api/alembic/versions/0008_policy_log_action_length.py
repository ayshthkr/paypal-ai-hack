"""Allow the full policy action labels in the audit log."""

from alembic import op
import sqlalchemy as sa


revision = "0008_policy_log_action_length"
down_revision = "0007_policy_log_column_lengths"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("policy_decision_logs") as batch_op:
        batch_op.alter_column(
            "chosen_action",
            existing_type=sa.String(length=32),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    # The full policy action label exceeds 32 characters, so shrinking this
    # column could make existing audit rows invalid.
    pass
