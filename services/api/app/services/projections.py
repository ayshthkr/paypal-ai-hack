"""Refresh durable forecasts and client signals in the writing transaction."""
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from app.models import Invoice, Payment, ReadModel


def refresh(db: Session, client_ids: set[str] | None = None, as_of=None):
    from app.main import _prepare_portfolio_context, _activity_for_source, _drift_for, _today, PORTFOLIO_CONTEXT_KEY, _REQUEST_DEMO_AS_OF
    from app.services.forecast import forecast_invoice
    from app.services.policy import CLOSED_STATUSES
    from app.http_cache import invalidate
    invalidate()
    db.flush()
    db.info.pop(PORTFOLIO_CONTEXT_KEY, None)
    rows = list(db.scalars(select(Invoice).options(joinedload(Invoice.client))).unique())
    db.info["precomputing"] = True
    clock_token = _REQUEST_DEMO_AS_OF.set(as_of) if as_of else None
    try:
        _prepare_portfolio_context(db, rows)
        sources = {row.source for row in rows if client_ids is None or row.client_id in client_ids}
        activity = {source: _activity_for_source(db, source) for source in sources}
        for source, probabilities in activity.items():
            db.merge(ReadModel(key=f"activity:{source}", as_of=as_of or _today(db, source), version="1", payload=probabilities))
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
            summary = {key: value for key, value in payload.items() if key in {
                "quantiles", "probabilities", "probability_by_due", "model", "model_version", "limited_history",
                "display_label", "collection_probability", "reasons", "history_count", "scoring_date"}}
            db.merge(ReadModel(key=f"forecast_summary:{row.id}", as_of=day, version=str(row.material_version), payload=summary))
        db.flush()
        db.info["projection_was_refreshed"] = True
    finally:
        if clock_token is not None:
            _REQUEST_DEMO_AS_OF.reset(clock_token)
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
