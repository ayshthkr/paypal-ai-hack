"""Use an explicit logical day for review counts across UTC midnight."""
from datetime import timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from alembic import op
import sqlalchemy as sa
from app.config import get_settings
revision = "0013_policy_date"
down_revision = "0012_unique_proposals"
branch_labels = depends_on = None


def upgrade():
    op.add_column("recommendations", sa.Column("policy_date", sa.Date()))
    op.create_index("ix_recommendations_policy_date", "recommendations", ["policy_date"])
    table = sa.table("recommendations", sa.column("id"), sa.column("created_at", sa.DateTime(timezone=True)), sa.column("policy_date", sa.Date()))
    bind = op.get_bind()
    try:
        zone = ZoneInfo(get_settings().merchant_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    for identifier, created in bind.execute(sa.select(table.c.id,table.c.created_at)):
        if created:
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            bind.execute(table.update().where(table.c.id == identifier).values(policy_date=created.astimezone(zone).date()))


def downgrade():
    op.drop_index("ix_recommendations_policy_date", "recommendations")
    op.drop_column("recommendations", "policy_date")
