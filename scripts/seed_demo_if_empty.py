"""Seed the public synthetic demo once, without resetting persisted records."""

from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Invoice
from scripts.seed_demo import seed


def seed_if_empty() -> dict[str, object]:
    db = SessionLocal()
    try:
        has_demo_invoice = db.scalar(
            select(Invoice.id).where(Invoice.source == "demo").limit(1)
        )
    finally:
        db.close()

    if has_demo_invoice:
        return {"seeded": False, "reason": "synthetic demo data already exists"}

    return {"seeded": True, **seed()}


if __name__ == "__main__":
    print(seed_if_empty())
