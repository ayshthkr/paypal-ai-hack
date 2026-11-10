from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from lightgbm import Booster
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Invoice, Payment, Prediction
from ml.features import FEATURES, FEATURE_SCHEMA_VERSION, features

HORIZON = 180


def _as_record(invoice: Invoice, payments: list[Payment]) -> dict:
    settled_at = None
    if invoice.status == "PAID" and payments:
        settled_at = max(payment.occurred_at.date() for payment in payments)
    ordered_payments = sorted(payments, key=lambda payment: payment.occurred_at)
    if invoice.status == "PARTIALLY_PAID":
        partial_payments = ordered_payments
    elif invoice.status == "PAID" and len(ordered_payments) > 1:
        partial_payments = ordered_payments[:-1]
    else:
        partial_payments = [payment for payment in ordered_payments if payment.provider_payment_id.startswith("demo-partial-")]
    partial_at = max((payment.occurred_at.date() for payment in partial_payments), default=None)
    partial_amount = sum((payment.amount for payment in partial_payments), start=invoice.total * 0)
    return {
        "id": invoice.id,
        "client_id": invoice.client_id,
        "issue_date": invoice.issue_date,
        "due_date": invoice.due_date,
        "total": invoice.total,
        "paid": invoice.paid,
        "outstanding": invoice.outstanding,
        "currency": invoice.currency,
        "status": invoice.status,
        "viewed_at": invoice.viewed_at.date() if invoice.viewed_at else None,
        "viewed_by_recipient": invoice.viewed_by_recipient,
        "viewed_observed_at": invoice.viewed_observed_at.date() if invoice.viewed_observed_at else None,
        "partial_payment_allowed": invoice.partial_payment_allowed,
        "payment_term_type": invoice.payment_term_type,
        "reminder_events": invoice.reminder_events or [],
        "partial_at": partial_at,
        "partial_amount": partial_amount,
        "settled_at": settled_at,
        "paid_at": settled_at,
        "cancelled_at": invoice.synced_at.date() if invoice.status in {"CANCELLED", "VOIDED", "DELETED"} and invoice.synced_at else None,
    }


def _file_revision(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _load_artifacts() -> tuple[Booster | None, list[float], str, str, dict, dict]:
    settings = get_settings()
    path = Path(settings.model_path)
    schema_path = Path(settings.feature_schema_path)
    metadata_path = path.with_name("model_metadata.json")
    baseline_path = path.with_name("kaplan_meier.json")
    calibration_path = path.with_name("calibration.json")
    revision = tuple(_file_revision(item) for item in (path, metadata_path, baseline_path, calibration_path, schema_path))
    return _load_artifacts_cached(str(path), str(schema_path), revision)


@lru_cache(maxsize=4)
def _load_artifacts_cached(model_path: str, schema_file: str, revision: tuple) -> tuple[Booster | None, list[float], str, str, dict, dict]:
    path = Path(model_path)
    schema_path = Path(schema_file)
    metadata_path = path.with_name("model_metadata.json")
    baseline_path = path.with_name("kaplan_meier.json")
    calibration_path = path.with_name("calibration.json")
    model = None
    baseline = [0.0075] * HORIZON
    active = "kaplan_meier"
    version = "pooled-km-default-v1"
    metadata = {}
    calibration = {}
    try:
        if baseline_path.exists():
            loaded = json.loads(baseline_path.read_text())
            baseline = [float(x) for x in loaded.get("hazards", [])][:HORIZON]
            baseline += [0.0] * (HORIZON - len(baseline))
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text())
            active = str(metadata.get("active_model", active))
            version = str(metadata.get("model_version", version))
        if calibration_path.exists():
            calibration = json.loads(calibration_path.read_text())
        schema = json.loads(schema_path.read_text()) if schema_path.exists() else {}
        schema_compatible = schema.get("version") == FEATURE_SCHEMA_VERSION and schema.get("features") == FEATURES
        if active == "lightgbm" and path.exists() and schema_compatible:
            model = Booster(model_file=str(path))
        elif active == "lightgbm":
            active = "kaplan_meier"
            version = "pooled-km-incompatible-artifact-v1"
    except (OSError, ValueError, TypeError):
        model = None
        active = "kaplan_meier"
        version = "pooled-km-fallback-v1"
    return model, baseline, active, version, metadata, calibration


def _survival(hazards: list[float]) -> list[float]:
    curve = [1.0]
    value = 1.0
    for hazard in hazards[:HORIZON]:
        value *= 1.0 - min(1.0, max(0.0, hazard))
        curve.append(float(value))
    while len(curve) < HORIZON + 1:
        curve.append(curve[-1])
    return curve


def _quantile(curve: list[float], threshold: float, today: date) -> dict | None:
    day = next((index for index, survival in enumerate(curve) if index > 0 and survival <= threshold), None)
    if day is None:
        return None
    return {"days": day, "date": (today + timedelta(days=day)).isoformat()}


def forecast_invoice(
    db: Session, invoice: Invoice, today: date | None = None, persist: bool = True,
    scenario: dict | None = None,
) -> dict:
    today = today or date.today()
    client_invoices = list(db.scalars(select(Invoice).where(Invoice.client_id == invoice.client_id)).all())
    invoice_ids = [item.id for item in client_invoices]
    payments = list(db.scalars(select(Payment).where(Payment.invoice_id.in_(invoice_ids))).all()) if invoice_ids else []
    payments_by_invoice: dict[str, list[Payment]] = {}
    for payment in payments:
        payments_by_invoice.setdefault(payment.invoice_id, []).append(payment)
    records = [_as_record(item, payments_by_invoice.get(item.id, [])) for item in client_invoices]
    record_by_id = {row["id"]: row for row in records}
    target = record_by_id[invoice.id]
    if scenario:
        target = {**target, **scenario}
        records = [target if row["id"] == invoice.id else row for row in records]
    history = [row for row in records if row["settled_at"] and row["settled_at"] <= today]
    base_features = features(target, today, history, records)
    model, baseline, active_model, model_version, metadata, calibration = _load_artifacts()
    use_model = model is not None and base_features["history_count"] >= 3

    fingerprint_raw = json.dumps({
        "invoice_version": invoice.material_version,
        "client_invoices": sorted((
            item.id, item.material_version, str(item.total), str(item.paid), str(item.outstanding), item.status,
            item.issue_date.isoformat(), item.due_date.isoformat(), item.viewed_at.isoformat() if item.viewed_at else None,
            item.viewed_by_recipient, item.viewed_observed_at.isoformat() if item.viewed_observed_at else None,
            item.partial_payment_allowed, item.payment_term_type, item.reminder_step, item.reminder_events or [],
        ) for item in client_invoices),
        "payments": sorted((p.provider_payment_id, str(p.amount), p.occurred_at.isoformat()) for p in payments),
        "today": today.isoformat(),
    }, sort_keys=True)
    fingerprint = hashlib.sha256(fingerprint_raw.encode()).hexdigest()
    existing = None
    if persist:
        existing = db.scalar(select(Prediction).where(
            Prediction.invoice_id == invoice.id,
            Prediction.scoring_date == today,
            Prediction.fingerprint == fingerprint,
            Prediction.model_version == model_version,
        ))
    if existing:
        curve = existing.curve
        quantiles = existing.quantiles
        probabilities = {key: value for key, value in existing.probabilities.items() if key in {"7d", "14d", "30d"}}
        reasons = existing.reasons
        model_label = existing.model_label
        history_count = existing.history_count
        scored_at = existing.scored_at
        if scored_at.tzinfo is None:
            scored_at = scored_at.replace(tzinfo=timezone.utc)
    else:
        curve = None
    hazards: list[float] = []
    if not existing and use_model:
        matrix = []
        for offset in range(HORIZON):
            row = features(target, today + timedelta(days=offset), history, records)
            matrix.append([row[name] for name in FEATURES])
        try:
            hazards = np.asarray(model.predict(np.asarray(matrix, dtype=np.float32)), dtype=float).tolist()
        except (ValueError, RuntimeError):
            use_model = False
    if not existing and not use_model:
        age = max(0, (today - invoice.issue_date).days)
        hazards = [baseline[min(age + offset, HORIZON - 1)] for offset in range(HORIZON)]
    if not existing:
        hazards = [min(1.0, max(0.0, float(hazard))) for hazard in hazards]
        curve = _survival(hazards)
        model_label = active_model if use_model else "kaplan_meier"
        history_count = int(base_features["history_count"])
        late_rate = base_features["history_late_rate"]
        reasons = [
            f"{history_count} prior fully settled invoices were known at scoring time.",
            f"Prior late-payment share: {late_rate:.0%}." if history_count else "No prior settled-payment history; using pooled Kaplan–Meier baseline.",
            f"Current open balance: {invoice.currency} {invoice.outstanding:,.2f}.",
        ]
        if history_count >= 3 and base_features["recent_delay_days"] > 0:
            reasons.append(f"Recent settled invoices had a median delay of {base_features['recent_delay_days']:.0f} days.")
        overdue_days_for_reason = max(0, (today - invoice.due_date).days)
        if overdue_days_for_reason:
            reasons.append(f"Invoice is already {overdue_days_for_reason} days overdue.")
        quantiles = {
            "p10": _quantile(curve, 0.9, today),
            "p50": _quantile(curve, 0.5, today),
            "p90": _quantile(curve, 0.1, today),
        }
        probabilities = {f"{day}d": round(1.0 - curve[day], 6) for day in (7, 14, 30)}
        if calibration.get("applied") and probabilities.get("14d") is not None:
            thresholds_x = np.asarray(calibration.get("thresholds_x", [0.0, 1.0]), dtype=float)
            thresholds_y = np.asarray(calibration.get("thresholds_y", [0.0, 1.0]), dtype=float)
            if len(thresholds_x) and len(thresholds_x) == len(thresholds_y):
                probabilities["14d"] = round(float(np.interp(probabilities["14d"], thresholds_x, thresholds_y)), 6)
        scored_at = datetime.now(timezone.utc)
        if persist:
            db.add(Prediction(
                invoice_id=invoice.id, scoring_date=today, fingerprint=fingerprint,
                model_version=model_version, feature_schema_version=FEATURE_SCHEMA_VERSION,
                model_label=model_label, history_count=history_count, scored_at=scored_at,
                curve=curve, quantiles=quantiles, probabilities=probabilities, reasons=reasons,
            ))
            db.flush()
    overdue_days = max(0, (today - invoice.due_date).days)
    return {
        "curve": curve,
        "quantiles": quantiles,
        "probabilities": probabilities,
        "probability_by_due": round(1.0 - curve[min(HORIZON, max(0, (invoice.due_date - today).days))], 6) if invoice.due_date >= today else 0.0,
        "reasons": reasons,
        "model": model_label,
        "model_version": model_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_metadata": {
            "model_version": model_version,
            "feature_list": metadata.get("feature_list", FEATURES),
            "training_date_range": metadata.get("training_date_range"),
            "training_cutoff": metadata.get("training_cutoff"),
            "training_seed": metadata.get("training_seed", metadata.get("seed")),
            "trained_at": metadata.get("trained_at"),
        },
        "scoring_date": today.isoformat(),
        "scored_at": scored_at.isoformat() if isinstance(scored_at, datetime) else scored_at,
        "forecast_horizon_days": HORIZON,
        "already_overdue": overdue_days > 0,
        "history_count": history_count,
        "limited_history": history_count < 3,
    }
