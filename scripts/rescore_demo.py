from __future__ import annotations

import json
from collections import Counter

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Client, DemoState, Invoice
from app.services.forecast import forecast_invoice
from app.services.projections import refresh
from app.services.policy import CLOSED_STATUSES
from ml.synthetic import OPEN_BUCKET_COUNTS, OPEN_COUNT


def _age_bucket(days_until_due: int) -> str:
    if days_until_due > 0:
        return "not_due"
    overdue = -days_until_due
    if overdue <= 30:
        return "late_1_30"
    if overdue < 90:
        return "late_31_89"
    return "late_90_plus"


def rescore_and_report(validate_seed: bool = True) -> dict:
    db = SessionLocal()
    try:
        state = db.get(DemoState, "demo")
        if state is None:
            raise RuntimeError("Demo clock is missing; run make seed-demo first.")
        as_of = state.as_of_date
        refresh(db)
        invoices = list(db.scalars(select(Invoice).where(Invoice.source == "demo")).all())
        open_invoices = [row for row in invoices if row.status not in CLOSED_STATUSES and row.outstanding > 0]
        bucket_counts = Counter(_age_bucket((row.due_date - as_of).days) for row in open_invoices)
        forecasts = [forecast_invoice(db, row, today=as_of, persist=True) for row in open_invoices]
        db.commit()
        if validate_seed:
            from app.services.demo_snapshot import capture
            capture(db)

        # Report the projections just refreshed from actual persisted state.
        # Re-generating the fixture here would ignore simulated payments and fit
        # BG/NBD a second time during every service restart.
        drift_results = [client.drift for client in db.scalars(select(Client).where(Client.source == "demo"))]
        signal_counts = Counter(row["signal_type"] for row in drift_results)
        drifting_clients = sum(signal_counts[name] for name in (
            "paying_later", "later_than_usual", "gone_quiet", "habitually_late",
        ))
        in_horizon = sum(item["quantiles"].get("p50") is not None for item in forecasts)
        share_in_horizon = in_horizon / len(forecasts) if forecasts else 0.0
        invoice_age_days = [(as_of - row.issue_date).days for row in open_invoices]
        report = {
            "as_of_date": as_of.isoformat(),
            "client_count": len({row.client_id for row in invoices}),
            "invoice_count": len(invoices),
            "open_count": len(open_invoices),
            "open_age_buckets": {name: bucket_counts.get(name, 0) for name in OPEN_BUCKET_COUNTS},
            "drifting_client_count": drifting_clients,
            "gone_quiet_client_count": signal_counts.get("gone_quiet", 0),
            "drift_signal_counts": dict(sorted(signal_counts.items())),
            "activity_model": "BG/NBD",
            "forecasts_with_p50_inside_180_days": in_horizon,
            "forecast_inside_horizon_share": round(share_in_horizon, 4),
            "oldest_open_invoice_age_days": max(invoice_age_days, default=0),
            "forecast_model_versions": sorted({item["model_version"] for item in forecasts}),
        }
        print(json.dumps(report, indent=2))

        if validate_seed:
            expected_buckets = OPEN_BUCKET_COUNTS
            age_mix_ok = all(
                abs(bucket_counts.get(name, 0) / max(1, len(open_invoices)) - target / OPEN_COUNT) <= 0.05
                for name, target in expected_buckets.items()
            )
            if not 40 <= len(open_invoices) <= 60 or len(open_invoices) != OPEN_COUNT:
                raise RuntimeError(f"Open invoice count outside target: {len(open_invoices)}")
            if not age_mix_ok:
                raise RuntimeError(f"Open invoice age mix outside target: {dict(bucket_counts)}")
            if drifting_clients < 1:
                raise RuntimeError(f"Synthetic paying-later signal count outside target: {drifting_clients}")
            if not 1500 <= len(invoices) <= 3000:
                raise RuntimeError(f"Invoice count outside target: {len(invoices)}")
            if share_in_horizon <= 0.5:
                raise RuntimeError(f"Too few open invoices have a p50 inside the 180-day horizon: {share_in_horizon:.1%}")
            if report["oldest_open_invoice_age_days"] > 140:
                raise RuntimeError(f"Open invoice age exceeds the 120-day cap plus one collections case: {report['oldest_open_invoice_age_days']} days")
            if sum(age > 120 for age in invoice_age_days) > 1:
                raise RuntimeError("More than one open invoice exceeds the 120-day age cap.")
        return report
    finally:
        db.close()


if __name__ == "__main__":
    rescore_and_report()
