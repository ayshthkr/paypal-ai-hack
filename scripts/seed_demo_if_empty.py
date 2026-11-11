"""Seed the public synthetic demo once, without resetting persisted records."""

from __future__ import annotations

from sqlalchemy import delete, func, or_, select, update

from app.db import SessionLocal
from app.models import Client, DemoState, Invoice, ReadModel
from ml.synthetic import resolve_as_of
from app.services.demo_snapshot import SNAPSHOT_VERSION
from app.services.forecast_revision import FORECAST_REVISION
from scripts.seed_demo import seed


def _invalidate_stale_forecast_projections(db) -> int:
    inference_revision = ReadModel.payload["inference_revision"].as_string()
    stale_revision = or_(inference_revision.is_(None), inference_revision != FORECAST_REVISION)
    result = db.execute(delete(ReadModel).where(
        (ReadModel.key.like("forecast:%") | ReadModel.key.like("forecast_summary:%")) & stale_revision
    ))
    invalidated = result.rowcount or 0
    if invalidated:
        db.commit()
    return invalidated


def seed_if_empty() -> dict[str, object]:
    db = SessionLocal()
    try:
        invoice_count = db.scalar(select(func.count(Invoice.id)).where(Invoice.source == "demo")) or 0
        client_count = db.scalar(select(func.count(Client.id)).where(Client.source == "demo")) or 0
        state = db.get(DemoState, "demo")
        seeded_as_of = state.as_of_date if state else None
        snapshot_version = db.scalar(select(ReadModel.version).where(
            ReadModel.key == "demo:snapshot", ReadModel.as_of == seeded_as_of,
        )) if seeded_as_of else None
        today = resolve_as_of()
        current = bool(
            invoice_count >= 1500
            and client_count >= 40
            and snapshot_version == SNAPSHOT_VERSION
        )
        invalidated = _invalidate_stale_forecast_projections(db)

        if current and state and seeded_as_of and seeded_as_of < today:
            newer_snapshot_version = db.scalar(select(ReadModel.version).where(
                ReadModel.key == "demo:snapshot", ReadModel.as_of == today,
            ))
            if newer_snapshot_version is None:
                db.execute(update(ReadModel).where(
                    ReadModel.key == "demo:snapshot", ReadModel.as_of == seeded_as_of,
                ).values(as_of=today))
                state.as_of_date = today
                db.commit()
            elif newer_snapshot_version == SNAPSHOT_VERSION:
                db.execute(delete(ReadModel).where(
                    ReadModel.key == "demo:snapshot", ReadModel.as_of == seeded_as_of,
                ))
                state.as_of_date = today
                db.commit()
            else:
                current = False
    finally:
        db.close()

    if current:
        return {
            "seeded": False,
            "reason": "synthetic demo data is current; the demo clock advances without regenerating history",
            "clients": client_count,
            "invoices": invoice_count,
            "as_of": today.isoformat(),
            "invalidated_forecast_projections": invalidated,
        }

    seeded = seed()
    return {
        "seeded": True,
        "previous_clients": client_count,
        "previous_invoices": invoice_count,
        "seed": seeded,
        "projections": "forecast and drift read models are computed on demand",
        "invalidated_forecast_projections": invalidated,
    }


if __name__ == "__main__":
    print("Checking demo snapshot and forecast cache revisions.", flush=True)
    print(seed_if_empty(), flush=True)
