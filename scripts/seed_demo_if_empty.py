"""Seed the public synthetic demo once, without resetting persisted records."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Client, DemoState, Invoice
from scripts.rescore_demo import rescore_and_report
from scripts.seed_demo import seed


def seed_if_empty() -> dict[str, object]:
    db = SessionLocal()
    try:
        invoice_count = db.scalar(select(func.count(Invoice.id)).where(Invoice.source == "demo")) or 0
        client_count = db.scalar(select(func.count(Client.id)).where(Client.source == "demo")) or 0
        state = db.get(DemoState, "demo")
        as_of = state.as_of_date if state else None
    finally:
        db.close()

    if invoice_count >= 1500 and client_count >= 40:
        return {"seeded": False, "reason": "synthetic demo data already meets the current dataset size", "clients": client_count, "invoices": invoice_count, "as_of": as_of.isoformat() if as_of else None}

    seeded = seed()
    report = rescore_and_report()
    return {"seeded": True, "previous_clients": client_count, "previous_invoices": invoice_count,
            "seed": seeded, "sanity_report": report}


if __name__ == "__main__":
    print(seed_if_empty())
