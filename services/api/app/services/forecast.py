from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
from lightgbm import Booster
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.dates import business_date, event_date
from app.services.forecast_revision import FORECAST_REVISION
from app.telemetry import timed
from app.config import get_settings
from app.models import Invoice, Payment, Prediction, ReadModel
from ml.evaluation import calibrated_band
from ml.features import FEATURES, FEATURE_SCHEMA_VERSION, features
from ml.scoring import (
    apply_promise_scenario as _apply_promise_scenario,
    apply_production_overlays,
    calibrate_survival_curve as _calibrate_survival_curve,  # noqa: F401 - compatibility helper
    collection_probability,
    conditional_tail,  # noqa: F401 - compatibility helper
    conditional_survival_from_absolute,
    survival_from_hazards as _survival,
)
from ml.survival_metrics import predict_cox_survival

HORIZON = 180
DISPLAY_CURVE_POINTS = 60


def _display_curve(curve: list[float]) -> list[float]:
    """Keep the forecast payload compact while retaining the full-resolution fit in storage."""
    if len(curve) <= DISPLAY_CURVE_POINTS:
        return curve
    last = len(curve) - 1
    indexes = [round(index * last / (DISPLAY_CURVE_POINTS - 1)) for index in range(DISPLAY_CURVE_POINTS)]
    return [curve[index] for index in indexes]


def _consistent_quantiles(quantiles: dict, probabilities: dict, today: date) -> dict:
    """Reject quantiles whose ordering contradicts their source survival curve."""
    del probabilities, today
    output = dict(quantiles)
    ordered = [output.get(key) for key in ("p10", "p50", "p90")]
    days = [value["days"] for value in ordered if value and value.get("days") is not None]
    if days != sorted(days):
        raise ValueError("Payment quantiles must be monotone and come from the same survival curve.")
    return output


def _as_record(invoice: Invoice, payments: list[Payment]) -> dict:
    settled_at = None
    if invoice.status == "PAID" and payments:
        settled_at = max(business_date(payment.occurred_at) for payment in payments)
    ordered_payments = sorted(payments, key=lambda payment: payment.occurred_at)
    if invoice.status == "PARTIALLY_PAID":
        partial_payments = ordered_payments
    elif invoice.status == "PAID" and len(ordered_payments) > 1:
        partial_payments = ordered_payments[:-1]
    else:
        partial_payments = [payment for payment in ordered_payments if payment.provider_payment_id.startswith("demo-partial-")]
    partial_at = max((business_date(payment.occurred_at) for payment in partial_payments), default=None)
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
        "disputed": invoice.disputed,
        "promised_payment_date": invoice.promised_payment_date,
        "viewed_at": business_date(invoice.viewed_at) if invoice.viewed_at else None,
        "viewed_by_recipient": bool(invoice.viewed_at or invoice.viewed_by_recipient),
        "viewed_observed_at": business_date(invoice.viewed_observed_at) if invoice.viewed_observed_at else None,
        "partial_payment_allowed": invoice.partial_payment_allowed,
        "payment_term_type": invoice.payment_term_type,
        "reminder_events": [{**event, "sent_at": day.isoformat()} if isinstance(event, dict) and (day := event_date(event.get("sent_at"))) else event
                            for event in invoice.reminder_events or []],
        "partial_at": partial_at,
        "partial_amount": partial_amount,
        "settled_at": settled_at,
        "paid_at": settled_at,
        "cancelled_at": business_date(invoice.synced_at) if invoice.status in {"CANCELLED", "VOIDED", "DELETED"} and invoice.synced_at else None,
    }


def _file_revision(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _artifact_revision() -> str:
    settings = get_settings()
    model_path = Path(settings.model_path)
    schema_path = Path(settings.feature_schema_path)
    paths = (
        model_path, model_path.with_name("model_metadata.json"),
        model_path.with_name("kaplan_meier.json"), model_path.with_name("calibration.json"),
        model_path.with_name("cox_model.json"),
        schema_path,
    )
    signatures = [(str(path), _file_revision(path)) for path in paths]
    return hashlib.sha256(json.dumps(signatures, separators=(",", ":")).encode()).hexdigest()


@timed("model_load")
def _load_artifacts() -> tuple[Booster | dict | None, list[float], str, str, dict, dict]:
    settings = get_settings()
    path = Path(settings.model_path)
    schema_path = Path(settings.feature_schema_path)
    metadata_path = path.with_name("model_metadata.json")
    baseline_path = path.with_name("kaplan_meier.json")
    calibration_path = path.with_name("calibration.json")
    revision = tuple(_file_revision(item) for item in (
        path, metadata_path, baseline_path, calibration_path, path.with_name("cox_model.json"), schema_path,
    ))
    return _load_artifacts_cached(str(path), str(schema_path), revision)


@lru_cache(maxsize=4)
def _load_artifacts_cached(model_path: str, schema_file: str, revision: tuple) -> tuple[Booster | dict | None, list[float], str, str, dict, dict]:
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
            version = f"{version}-{hashlib.sha256(path.read_bytes()).hexdigest()[:12]}"
        elif active == "cox" and path.with_name("cox_model.json").exists() and schema_compatible:
            cox_path = path.with_name("cox_model.json")
            cox_payload = json.loads(cox_path.read_text())
            model = {
                **cox_payload,
                "mean": np.asarray(cox_payload["mean"], dtype=float),
                "scale": np.asarray(cox_payload["scale"], dtype=float),
                "beta": np.asarray(cox_payload["beta"], dtype=float),
                "baseline_cumulative_hazard": np.asarray(cox_payload["baseline_cumulative_hazard"], dtype=float),
            }
            version = f"{version}-{hashlib.sha256(cox_path.read_bytes()).hexdigest()[:12]}"
        elif active == "lightgbm":
            active = "kaplan_meier"
            version = "pooled-km-incompatible-artifact-v1"
        elif active == "cox":
            active = "kaplan_meier"
            version = "pooled-km-incompatible-artifact-v1"
    except (OSError, ValueError, TypeError, KeyError):
        model = None
        active = "kaplan_meier"
        version = "pooled-km-fallback-v1"
    return model, baseline, active, version, metadata, calibration


def _quantile(curve: list[float], threshold: float, today: date) -> dict | None:
    day = next((index for index, survival in enumerate(curve) if index > 0 and survival <= threshold), None)
    if day is None:
        return None
    previous, current = curve[day - 1], curve[day]
    crossing = day - 1 + (previous - threshold) / max(1e-12, previous - current)
    return {"days": round(crossing, 2), "date": (today + timedelta(days=math.ceil(crossing))).isoformat()}


def _weekly_settlement_probabilities(curve: list[float], today: date) -> list[dict]:
    """Aggregate daily settlement mass into calendar weeks without assigning principal to p50."""
    weekly: dict[date, float] = {}
    for day in range(1, min(HORIZON, len(curve) - 1) + 1):
        probability = max(0.0, float(curve[day - 1]) - float(curve[day]))
        if probability <= 0:
            continue
        settlement_date = today + timedelta(days=day)
        week_start = settlement_date - timedelta(days=settlement_date.weekday())
        weekly[week_start] = weekly.get(week_start, 0.0) + probability
    return [
        {"week_start": week_start.isoformat(), "probability": round(probability, 10)}
        for week_start, probability in sorted(weekly.items())
    ]


@timed("scoring")
def forecast_invoice(
    db: Session, invoice: Invoice, today: date | None = None, persist: bool = True,
    scenario: dict | None = None, detail: bool = True,
) -> dict:
    today = today or date.today()
    artifact_revision = _artifact_revision()
    if persist and not scenario and not db.info.get("precomputing"):
        context = db.info.get("latepayer_portfolio_context")
        key = f"forecast:{invoice.id}" if detail else f"forecast_summary:{invoice.id}"
        snapshot = (context["read_models"].get((key, today)) if context else None) or db.get(ReadModel, (key, today))
        payload = snapshot.payload if snapshot else None
        if (snapshot and snapshot.version == str(invoice.material_version) and payload
                and payload.get("inference_revision") == FORECAST_REVISION
                and payload.get("artifact_revision") == artifact_revision):
            return snapshot.payload
    portfolio = db.info.get("latepayer_portfolio_context")
    if portfolio is not None and invoice.client_id in portfolio["records_by_client"]:
        client_invoices = portfolio["invoices_by_client"].get(invoice.client_id, [])
        payments_by_invoice = portfolio["payments_by_invoice"]
        payments = [payment for item in client_invoices for payment in payments_by_invoice.get(item.id, [])]
        records = portfolio["records_by_client"].get(invoice.client_id, [])
    else:
        portfolio = None
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
    use_model = model is not None and (active_model == "cox" or base_features["history_count"] >= 3)
    if not use_model:
        baseline_version = metadata.get("kaplan_meier_model_version", "pooled-km-default-v1")
        baseline_path = Path(get_settings().model_path).with_name("kaplan_meier.json")
        try:
            baseline_version = f"{baseline_version}-{hashlib.sha256(baseline_path.read_bytes()).hexdigest()[:12]}"
        except OSError:
            pass
        model_version = baseline_version

    fingerprint_raw = json.dumps({
        "invoice_version": invoice.material_version,
        "client_invoices": sorted((
            item.id, item.material_version, str(item.total), str(item.paid), str(item.outstanding), item.status,
            item.issue_date.isoformat(), item.due_date.isoformat(), item.viewed_at.isoformat() if item.viewed_at else None,
            item.viewed_by_recipient, item.viewed_observed_at.isoformat() if item.viewed_observed_at else None,
            item.partial_payment_allowed, item.payment_term_type, item.reminder_step, item.reminder_events or [],
            item.promised_payment_date.isoformat() if item.promised_payment_date else None,
        ) for item in client_invoices),
        "payments": sorted((p.provider_payment_id, str(p.amount), p.occurred_at.isoformat()) for p in payments),
        "today": today.isoformat(),
        "inference_revision": FORECAST_REVISION,
        "artifact_revision": artifact_revision,
    }, sort_keys=True)
    fingerprint = hashlib.sha256(fingerprint_raw.encode()).hexdigest()
    existing = None
    if persist:
        prediction_key = (invoice.id, today, fingerprint, model_version)
        if portfolio is not None:
            existing = portfolio["predictions_by_key"].get(prediction_key)
        else:
            existing = db.scalar(select(Prediction).where(
                Prediction.invoice_id == invoice.id,
                Prediction.scoring_date == today,
                Prediction.fingerprint == fingerprint,
                Prediction.model_version == model_version,
            ))
    if existing:
        curve = existing.curve
        quantiles = existing.quantiles
        probabilities = {key: value for key, value in existing.probabilities.items() if key in {"7d", "14d", "30d", "60d", "90d"}}
        for day in (60, 90):
            probabilities.setdefault(f"{day}d", round(1.0 - float(curve[day]), 6))
        reasons = existing.reasons
        model_label = existing.model_label
        history_count = existing.history_count
        scored_at = existing.scored_at
        if scored_at.tzinfo is None:
            scored_at = scored_at.replace(tzinfo=timezone.utc)
        _, promise_adjustment = _apply_promise_scenario(
            curve, target.get("promised_payment_date"), today,
            collection_probability(records, today) if max(0, (today - invoice.due_date).days) >= 90 else 1.0,
        )
    else:
        curve = None
    hazards: list[float] = []
    if not existing and use_model:
        try:
            if active_model == "cox":
                row = [base_features[name] for name in FEATURES]
                absolute_survival = predict_cox_survival(np.asarray([row], dtype=np.float32), model)[0]
                age = max(0, (today - target["issue_date"]).days)
                curve = conditional_survival_from_absolute(absolute_survival, age, HORIZON)
            else:
                matrix = []
                for offset in range(HORIZON):
                    row = features(target, today + timedelta(days=offset), history, records)
                    matrix.append([row[name] for name in FEATURES])
                hazards = np.asarray(model.predict(np.asarray(matrix, dtype=np.float32), num_threads=1), dtype=float).tolist()
                curve = _survival(hazards)
        except (ValueError, RuntimeError):
            use_model = False
    if not existing and (not use_model or curve is None):
        age = max(0, (today - target["issue_date"]).days)
        # Never repeat the final KM hazard: its risk set is exhausted.
        hazards = [baseline[age + offset] if age + offset < len(baseline) else 0.0 for offset in range(HORIZON)]
        curve = _survival(hazards)
    if not existing:
        if curve is None:
            hazards = [min(1.0, max(0.0, float(hazard))) for hazard in hazards]
            curve = _survival(hazards)
        assert curve is not None
        curve, _, promise_adjustment = apply_production_overlays(
            curve, target, records, history, today, calibration, HORIZON,
        )
        model_label = active_model if use_model else "kaplan_meier"
        history_count = int(base_features["history_count"])
        late_rate = base_features["history_late_rate"]
        reasons = [
            f"{history_count} prior fully settled invoices were known at scoring time.",
            f"Prior late-payment share: {late_rate:.0%} of prior settled invoices." if history_count else "No prior settled-payment history; using pooled Kaplan–Meier baseline.",
            f"Current open balance: {invoice.currency} {target['outstanding']:,.2f}.",
        ]
        if target["payment_term_type"] == "DUE_ON_RECEIPT" or target["due_date"] == target["issue_date"]:
            reasons.append("Due on receipt means there is no contractual days-to-pay estimate; this forecast estimates settlement timing from observed history and invoice facts.")
        if history_count >= 3 and base_features["recent_delay_days"] > 0:
            reasons.append(f"Recent settled invoices had a median delay of {base_features['recent_delay_days']:.0f} days.")
        overdue_days_for_reason = max(0, (today - target["due_date"]).days)
        if overdue_days_for_reason:
            reasons.append(f"Invoice is already {overdue_days_for_reason} days overdue.")
        if promise_adjustment.get("applied"):
            reasons.append(f"A client promise for {promise_adjustment['date']} is reflected as an illustrative 25% scenario adjustment.")
        quantiles = {
            "p10": _quantile(curve, 0.9, today),
            "p50": _quantile(curve, 0.5, today),
            "p90": _quantile(curve, 0.1, today),
        }
        probabilities = {f"{day}d": round(1.0 - curve[day], 6) for day in (7, 14, 30, 60, 90)}
        quantiles = _consistent_quantiles(quantiles, probabilities, today)
        scored_at = datetime.now(timezone.utc)
        if persist:
            prediction = Prediction(
                invoice_id=invoice.id, scoring_date=today, fingerprint=fingerprint,
                model_version=model_version, feature_schema_version=FEATURE_SCHEMA_VERSION,
                model_label=model_label, history_count=history_count, scored_at=scored_at,
                curve=curve, quantiles=quantiles, probabilities=probabilities, reasons=reasons,
            )
            db.add(prediction)
            if portfolio is not None:
                portfolio["predictions_by_key"][prediction_key] = prediction
            else:
                db.flush()
    interval = calibration.get("interval_calibration", {})
    currency_correction = interval.get("currency_corrections", {}).get(invoice.currency, {}).get(
        "correction_days", interval.get("correction_days", 0),
    )
    age_widening = math.ceil(max(0, max(0, (today - target["due_date"]).days) - 60) / 6)
    lower_day, median_day, upper_day = calibrated_band(curve, int(currency_correction) + age_widening)
    coverage_interval = {
        "lower_date": (today + timedelta(days=lower_day)).isoformat() if lower_day else None,
        "median_date": (today + timedelta(days=median_day)).isoformat() if median_day else None,
        "upper_date": (today + timedelta(days=upper_day)).isoformat() if upper_day else None,
        "target_coverage": interval.get("target", 0.8),
        "method": interval.get("method", "Uncalibrated survival interval"),
        "caveat": "Coverage calibration is paid-only; promise adherence and promise-adjusted coverage are not estimated from training data.",
    }
    overdue_days = max(0, (today - target["due_date"]).days)
    return {
        "curve": _display_curve(curve),
        "weekly_settlement_probabilities": _weekly_settlement_probabilities(curve, today),
        "unforecasted_probability": round(max(0.0, float(curve[-1])), 8),
        "quantiles": quantiles,
        "coverage_interval": coverage_interval,
        "promise_adjustment": promise_adjustment,
        "probabilities": probabilities,
        "probability_by_due": round(1.0 - curve[min(HORIZON, max(0, (target["due_date"] - today).days))], 6) if target["due_date"] >= today else 0.0,
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
        "inference_revision": FORECAST_REVISION,
        "artifact_revision": artifact_revision,
        "already_overdue": overdue_days > 0,
        "history_count": history_count,
        "limited_history": history_count < 5,
        "display_label": "Lower confidence · 60+ days overdue" if overdue_days >= 60 else None,
        "collection_probability": collection_probability(records, today) if overdue_days >= 90 else 1.0,
        "interval_method": calibration.get("interval_calibration", {}).get("method", "Raw survival quantiles"),
        "collection_method": "Beta-shrunk historical recovery; sparse tail evidence" if overdue_days >= 90 else None,
    }


def warm_model() -> None:
    model, *_ = _load_artifacts()
    if isinstance(model, Booster):
        model.predict(np.zeros((1, len(FEATURES)), dtype=np.float32), num_threads=1)
    elif isinstance(model, dict):
        predict_cox_survival(np.zeros((1, len(FEATURES)), dtype=np.float32), model)
