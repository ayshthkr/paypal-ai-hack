from __future__ import annotations

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import ActionAttempt, Client, Invoice, Payment, Prediction, Recommendation
from ml.synthetic import HISTORY_CUTOFF, generate_records, utc_datetime


def seed() -> dict[str, int]:
    records = generate_records()
    clients = {key: {"id": key, "name": row["client_name"], "profile": row["profile"]}
               for key, row in ((row["client_id"], row) for row in records)}
    db = SessionLocal()
    try:
        demo_invoice_ids = select(Invoice.id).where(Invoice.source == "demo")
        demo_recommendation_ids = select(Recommendation.id).where(Recommendation.invoice_id.in_(demo_invoice_ids))
        db.execute(delete(ActionAttempt).where(ActionAttempt.recommendation_id.in_(demo_recommendation_ids)))
        db.execute(delete(Recommendation).where(Recommendation.invoice_id.in_(demo_invoice_ids)))
        db.execute(delete(Prediction).where(Prediction.invoice_id.in_(demo_invoice_ids)))
        db.execute(delete(Payment).where(Payment.invoice_id.in_(demo_invoice_ids)))
        db.execute(delete(Invoice).where(Invoice.source == "demo"))
        db.execute(delete(Client).where(Client.source == "demo"))
        db.flush()
        for client in clients.values():
            db.add(Client(id=client["id"], display_name=client["name"], email=None,
                          source="demo", drift={"signal": "insufficient_history", "available": False,
                                                 "settled_count": 0, "message": "Drift is computed from settled invoices."}))
        db.flush()
        for row in records:
            db.add(Invoice(
                id=row["id"], invoice_number=row["number"], client_id=row["client_id"],
                issue_date=row["issue_date"], due_date=row["due_date"], total=row["total"], paid=row["paid"],
                outstanding=row["outstanding"], currency=row["currency"], status=row["status"],
                disputed=False, source="demo", provider_payload={}, synced_at=utc_datetime(HISTORY_CUTOFF), material_version=1,
            ))
        db.flush()
        for row in records:
            if row["paid"] <= 0:
                continue
            occurred = row["settled_at"] or row["partial_at"] or HISTORY_CUTOFF
            suffix = "full" if row["status"] == "PAID" else "partial"
            db.add(Payment(
                provider_payment_id=f"demo-{suffix}-{row['id']}", invoice_id=row["id"], amount=row["paid"],
                currency=row["currency"], occurred_at=utc_datetime(occurred), source="demo",
            ))
        db.commit()
        return {"clients": len(clients), "invoices": len(records), "payments": sum(row["paid"] > 0 for row in records),
                "cutoff": HISTORY_CUTOFF.isoformat()}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print(seed())
