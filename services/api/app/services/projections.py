"""Refresh durable forecasts and client signals in the writing transaction."""
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from app.models import Invoice, Payment, ReadModel


def refresh(db: Session, client_ids: set[str] | None = None, as_of=None):
    from app.main import _prepare_portfolio_context, _activity_for_source, _drift_for, _today, PORTFOLIO_CONTEXT_KEY
    from app.services.forecast import forecast_invoice
    from app.services.policy import CLOSED_STATUSES
    from app.http_cache import invalidate
    invalidate()
    db.flush()
    db.info.pop(PORTFOLIO_CONTEXT_KEY, None)
    rows = list(db.scalars(select(Invoice).options(joinedload(Invoice.client))).unique())
    _prepare_portfolio_context(db, rows)
    db.info["precomputing"] = True
    try:
        sources = {row.source for row in rows if client_ids is None or row.client_id in client_ids}
        activity = {source: _activity_for_source(db, source) for source in sources}
        clients = {row.client_id: row.client for row in rows if client_ids is None or row.client_id in client_ids}
        for client in clients.values():
            day = as_of or _today(db, client.source)
            payload = _drift_for(db, client, activity.get(client.source, {}).get(client.id))
            db.merge(ReadModel(key=f"drift:{client.id}", as_of=day, version="1", payload=payload))
        for row in rows:
            if row.client_id not in clients or row.status in CLOSED_STATUSES or row.outstanding <= 0:
                continue
            day = as_of or _today(db, row.source)
            payload = forecast_invoice(db, row, today=day)
            db.merge(ReadModel(key=f"forecast:{row.id}", as_of=day, version=str(row.material_version), payload=payload))
        db.flush()
    finally:
        db.info.pop("precomputing", None)
        db.info.pop(PORTFOLIO_CONTEXT_KEY, None)
        db.info.pop("projection_clients", None)


def collect_changed_clients(db, context, instances):
    if db.info.get("precomputing"):
        return
    changed = db.info.setdefault("projection_clients", set())
    for row in db.new | db.dirty | db.deleted:
        if isinstance(row, Invoice):
            changed.add(row.client_id)
        elif isinstance(row, Payment):
            invoice = db.get(Invoice, row.invoice_id)
            if invoice:
                changed.add(invoice.client_id)


def update_projections(db):
    if db.info.get("precomputing"):
        return
    db.flush()
    changed = db.info.get("projection_clients", set())
    if changed:
        refresh(db, changed)


def clear_pending(db):
    db.info.pop("projection_clients", None)
