"""Seed the public synthetic demo once, without resetting persisted records."""

from __future__ import annotations

from sqlalchemy import func, or_, select

from app.db import SessionLocal
from app.models import Client, DemoState, Invoice, ReadModel
from ml.synthetic import resolve_as_of
from app.services.demo_snapshot import SNAPSHOT_VERSION
from app.services.forecast import FORECAST_REVISION
from scripts.seed_demo import seed


def _invalidate_stale_forecast_projections(db) -> int:
    stale = []
    query = select(ReadModel).where(or_(
        ReadModel.key.like("forecast:%"),
        ReadModel.key.like("forecast_summary:%"),
    ))
    for projection in db.scalars(query):
        payload = projection.payload
        if not isinstance(payload, dict) or payload.get("inference_revision") != FORECAST_REVISION:
            stale.append(projection)
    for projection in stale:
        db.delete(projection)
    if stale:
        db.commit()
    return len(stale)


def seed_if_empty() -> dict[str, object]:
    db = SessionLocal()
    try:
        invoice_count = db.scalar(select(func.count(Invoice.id)).where(Invoice.source == "demo")) or 0
        client_count = db.scalar(select(func.count(Client.id)).where(Client.source == "demo")) or 0
        state = db.get(DemoState, "demo")
        as_of = state.as_of_date if state else None
        snapshot = db.get(ReadModel, ("demo:snapshot", as_of)) if as_of else None
        current = bool(snapshot and snapshot.version == SNAPSHOT_VERSION and as_of == resolve_as_of())
        invalidated = _invalidate_stale_forecast_projections(db)
    finally:
        db.close()

    if invoice_count >= 1500 and client_count >= 40 and current:
        return {
            "seeded": False,
            "reason": "synthetic demo data is current; forecast caches refresh lazily by inference revision",
            "clients": client_count,
            "invoices": invoice_count,
            "as_of": as_of.isoformat() if as_of else None,
            "invalidated_forecast_projections": invalidated,
        }

    seeded = seed()
    return {"seeded": True, "previous_clients": client_count, "previous_invoices": invoice_count,
            "seed": seeded, "invalidated_forecast_projections": invalidated,
            "projections": "forecast and drift read models are computed on demand"}


if __name__ == "__main__":
    print(seed_if_empty())
