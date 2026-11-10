"""Index dated provider events by their reconciled local invoice."""
from alembic import op
import sqlalchemy as sa
revision = "0011_event_invoice_index"
down_revision = "0010_ai_audit"
branch_labels = depends_on = None


def upgrade():
    op.add_column("invoice_events", sa.Column("invoice_id",sa.String(36)))
    op.create_index("ix_invoice_events_invoice_occurred","invoice_events",["invoice_id","occurred_at"], if_not_exists=True)
    bind = op.get_bind()
    events = sa.table("invoice_events",sa.column("id"),sa.column("raw_payload",sa.JSON()),sa.column("invoice_id"))
    invoices = sa.table("invoices",sa.column("id"),sa.column("paypal_id"))
    mapping = dict(bind.execute(sa.select(invoices.c.paypal_id,invoices.c.id)).all())
    for identifier, payload in bind.execute(sa.select(events.c.id,events.c.raw_payload)):
        resource = (payload or {}).get("resource", {})
        provider_id = (resource.get("invoice") or {}).get("id") or resource.get("id")
        if provider_id in mapping:
            bind.execute(events.update().where(events.c.id == identifier).values(invoice_id=mapping[provider_id]))


def downgrade():
    op.drop_index("ix_invoice_events_invoice_occurred","invoice_events")
    op.drop_column("invoice_events","invoice_id")
