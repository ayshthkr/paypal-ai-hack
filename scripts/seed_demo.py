from __future__ import annotations

import json

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import ActionAttempt, Client, DemoState, Invoice, Payment, PolicyDecisionLog, Prediction, Recommendation, ReplyTriage, now_utc
from ml.synthetic import OPEN_BUCKET_COUNTS, OPEN_COUNT, generate_records, resolve_as_of, utc_datetime


def seed(as_of=None) -> dict[str, object]:
    cutoff = resolve_as_of(as_of)
    records = generate_records(observation_cutoff=cutoff)
    clients = {key: {"id": key, "name": row["client_name"], "profile": row["profile"]}
               for key, row in ((row["client_id"], row) for row in records)}
    db = SessionLocal()
    try:
        previous_demo_state = db.get(DemoState, "demo")
        reset_completed_at = previous_demo_state.reset_completed_at if previous_demo_state else None
        demo_invoice_ids = select(Invoice.id).where(Invoice.source == "demo")
        demo_recommendation_ids = select(Recommendation.id).where(Recommendation.invoice_id.in_(demo_invoice_ids))
        db.execute(delete(ActionAttempt).where(ActionAttempt.recommendation_id.in_(demo_recommendation_ids)))
        db.execute(delete(Recommendation).where(Recommendation.invoice_id.in_(demo_invoice_ids)))
        db.execute(delete(PolicyDecisionLog).where(PolicyDecisionLog.invoice_id.in_(demo_invoice_ids)))
        db.execute(delete(ReplyTriage).where(ReplyTriage.invoice_id.in_(demo_invoice_ids)))
        db.execute(delete(Prediction).where(Prediction.invoice_id.in_(demo_invoice_ids)))
        db.execute(delete(Payment).where(Payment.invoice_id.in_(demo_invoice_ids)))
        db.execute(delete(Invoice).where(Invoice.source == "demo"))
        db.execute(delete(Client).where(Client.source == "demo"))
        db.merge(DemoState(id="demo", as_of_date=cutoff, seeded_at=now_utc(), reset_completed_at=reset_completed_at))
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
                disputed=False, source="demo", provider_payload={}, synced_at=utc_datetime(cutoff), material_version=1,
                viewed_at=utc_datetime(row["viewed_at"]) if row["viewed_at"] else None,
                viewed_by_recipient=bool(row["viewed_at"]),
                viewed_observed_at=utc_datetime(row["viewed_at"]) if row["viewed_at"] else None,
                reminder_step=max((3 if event["tone"] == "firm" else 2 for event in row["reminder_events"]), default=0),
                partial_payment_allowed=row["partial_payment_allowed"], payment_term_type=row["payment_term_type"],
                reminder_events=row["reminder_events"],
            ))
        db.flush()
        for row in records:
            if row["paid"] <= 0:
                continue
            if row["partial_at"] and row["partial_amount"] > 0:
                db.add(Payment(
                    provider_payment_id=f"demo-partial-{row['id']}", invoice_id=row["id"],
                    amount=row["partial_amount"], currency=row["currency"],
                    occurred_at=utc_datetime(row["partial_at"]), source="demo",
                ))
                remainder = row["paid"] - row["partial_amount"]
                if remainder > 0:
                    settled_at = row["settled_at"] or cutoff
                    db.add(Payment(
                        provider_payment_id=f"demo-settlement-{row['id']}", invoice_id=row["id"],
                        amount=remainder, currency=row["currency"], occurred_at=utc_datetime(settled_at), source="demo",
                    ))
            else:
                occurred = row["settled_at"] or row["partial_at"] or cutoff
                db.add(Payment(
                    provider_payment_id=f"demo-full-{row['id']}", invoice_id=row["id"], amount=row["paid"],
                    currency=row["currency"], occurred_at=utc_datetime(occurred), source="demo",
                ))
        db.commit()
        from app.services.demo_snapshot import capture
        capture(db)
        open_rows = [row for row in records if row["outstanding"] > 0 and not row["cancelled"]]
        buckets = {name: sum(row["open_age_bucket"] == name for row in open_rows) for name in OPEN_BUCKET_COUNTS}
        if not 40 <= len(open_rows) <= 60 or len(open_rows) != OPEN_COUNT or buckets != OPEN_BUCKET_COUNTS:
            raise RuntimeError(f"Synthetic open-book sanity check failed: {len(open_rows)} open, buckets={buckets}")
        if not 1500 <= len(records) <= 3000:
            raise RuntimeError(f"Synthetic dataset size out of range: {len(records)} invoices")
        return {
            "clients": len(clients), "invoices": len(records),
            "payments": sum(row["paid"] > 0 for row in records), "cutoff": cutoff.isoformat(),
            "open_count": len(open_rows), "open_age_buckets": buckets,
            "expected_drifting_clients": sum(client["profile"] == "deteriorating" for client in clients.values()),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print(json.dumps(seed(), indent=2))
