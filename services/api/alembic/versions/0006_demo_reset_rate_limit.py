"""Track the latest public synthetic demo reset."""

from alembic import op
import sqlalchemy as sa


revision = "0006_demo_reset_rate_limit"
down_revision = "0005_paypal_sync_forecast_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("demo_state", sa.Column("reset_completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("demo_state", "reset_completed_at")
