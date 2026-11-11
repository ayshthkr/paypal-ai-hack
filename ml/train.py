from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from lightgbm import LGBMClassifier, Booster
from sklearn.isotonic import IsotonicRegression
from sklearn.inspection import permutation_importance

from ml.policy_simulation import replay
from ml.evaluation import (
    fit_conformal, calibrated_band, correction_for_currency, grouped_errors, client_bootstrap,
    horizon_calibration, residual_drift, coverage_by_issue_month,
)
from ml.features import (
    FEATURES, FEATURE_SCHEMA_VERSION, GLOBAL_AMOUNT_MEDIAN_PRIOR, GLOBAL_DELAY_PRIOR_DAYS,
    GLOBAL_LATE_RATE_PRIOR, HISTORY_PRIOR_STRENGTH, features,
)
from ml.scoring import (
    apply_production_overlays, conditional_survival_from_absolute,
    survival_from_hazards,
)
from ml.survival_metrics import fit_cox, predict_cox_survival, time_dependent_auc, uno_concordance
from ml.drift import backtest_drift
from ml.synthetic import SEED, generate_records, resolve_as_of

HORIZON = 180


def _history(records: list[dict], client_id: str) -> list[dict]:
    return [row for row in records if row["client_id"] == client_id and row["settled_at"]]


def _duration(row: dict, observation_end: date) -> tuple[int, int]:
    end = min(observation_end, row["issue_date"] + timedelta(days=HORIZON))
    event_at = row["outcome_paid_at"]
    if event_at and event_at <= end:
        return max(1, (event_at - row["issue_date"]).days), 1
    return max(1, (end - row["issue_date"]).days), 0


def _row_features(records: list[dict], invoice: dict, as_of: date, frozen_history: list[dict] | None = None,
                  frozen_exposure: list[dict] | None = None,
                  client_records: list[dict] | None = None) -> list[float]:
    evidence = client_records if client_records is not None else [
        row for row in records if row["client_id"] == invoice["client_id"]
    ]
    history = frozen_history if frozen_history is not None else [row for row in evidence if row["settled_at"]]
    all_rows = frozen_exposure if frozen_exposure is not None else evidence
    # Final paid balances must never leak into historical risk rows.
    target = dict(invoice)
    target["outstanding"] = invoice["total"]
    if invoice.get("partial_at") and invoice["partial_at"] <= as_of:
        target["outstanding"] -= invoice.get("partial_amount", 0)
    values = features(target, as_of, history, all_rows)
    return [values[name] for name in FEATURES]


def make_risk_rows(records: list[dict], observation_end: date, eligible) -> tuple[np.ndarray, np.ndarray, list[tuple[dict, int, int]]]:
    X: list[list[float]] = []
    y: list[int] = []
    subjects = []
    by_client: dict[str, list[dict]] = {}
    for row in records:
        by_client.setdefault(row["client_id"], []).append(row)
    for invoice in records:
        if invoice["cancelled"] or not eligible(invoice):
            continue
        duration, event = _duration(invoice, observation_end)
        # Build each invoice-day row only from evidence available at the beginning of that day.
        for day_number in range(1, duration + 1):
            as_of = invoice["issue_date"] + timedelta(days=day_number - 1)
            X.append(_row_features(records, invoice, as_of, client_records=by_client[invoice["client_id"]]))
            y.append(int(event == 1 and day_number == duration))
        subjects.append((invoice, duration, event))
    return np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.int8), subjects


def km_hazards(subjects: list[tuple[dict, int, int]]) -> list[float]:
    hazards = []
    survival = 1.0
    for day in range(1, HORIZON + 1):
        risk = sum(duration >= day for _, duration, _ in subjects)
        events = sum(duration == day and event == 1 for _, duration, event in subjects)
        hazard = events / risk if risk else 0.0
        hazards.append(float(min(1.0, max(0.0, hazard))))
        survival *= 1.0 - hazards[-1]
        if risk == 0:
            hazards[-1] = 0.0
    return hazards


def _survival_from_hazards(hazards: list[float]) -> list[float]:
    return survival_from_hazards(hazards, HORIZON)


def _censor_survival(subjects: list[tuple[dict, int, int]]) -> list[float]:
    """Kaplan–Meier estimate of remaining uncensored probability G(t)."""
    result = [1.0]
    value = 1.0
    for day in range(1, HORIZON + 1):
        risk = sum(duration >= day for _, duration, _ in subjects)
        censored = sum(duration == day and event == 0 for _, duration, event in subjects)
        if risk:
            value *= 1.0 - censored / risk
        result.append(max(1e-6, value))
    return result


def predict_hazards(model: Booster, records: list[dict], invoice: dict, hazards_fallback: list[float],
                    issue_origin: bool = True, feature_names: list[str] | None = None,
                    records_by_client: dict[str, list[dict]] | None = None) -> list[float]:
    origin = invoice["issue_date"]
    client_records = (records_by_client or {}).get(invoice["client_id"])
    if client_records is None:
        client_records = [row for row in records if row["client_id"] == invoice["client_id"]]
    history = [row for row in client_records if row["settled_at"] and row["settled_at"] <= origin]
    # Freeze the knowledge state at the forecast origin; only age and calendar features advance.
    exposure = []
    for row in client_records:
        if row["issue_date"] <= origin:
            snapshot = dict(row)
            snapshot["settled_at"] = None
            snapshot["cancelled_at"] = None
            if row["outcome_paid_at"] and row["outcome_paid_at"] <= origin:
                snapshot["outstanding"] = 0
            elif row["partial_at"] and row["partial_at"] <= origin:
                snapshot["outstanding"] = row["total"] - row["partial_amount"]
            else:
                snapshot["outstanding"] = row["total"]
            exposure.append(snapshot)
    invoice = dict(invoice)
    if invoice.get("viewed_at") and invoice["viewed_at"] > origin:
        invoice["viewed_at"] = None
    if invoice.get("partial_at") and invoice["partial_at"] > origin:
        invoice["partial_at"], invoice["partial_amount"] = None, 0
    invoice["reminder_events"] = [event for event in invoice.get("reminder_events", []) if date.fromisoformat(event["sent_at"][:10]) <= origin]
    matrix = []
    for day_number in range(1, HORIZON + 1):
        as_of = origin + timedelta(days=day_number - 1)
        matrix.append(_row_features(records, invoice, as_of, history, exposure, client_records))
    names = feature_names or FEATURES
    raw = np.asarray(matrix, dtype=np.float32)
    indices = [FEATURES.index(name) for name in names]
    if model is None:
        hazards = hazards_fallback.copy()
    else:
        hazards = np.asarray(model.predict(raw[:, indices], num_threads=1), dtype=float).tolist()
    return [min(1.0, max(0.0, float(hazard))) for hazard in hazards]


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _records_as_of(records: list[dict], score_date: date) -> list[dict]:
    """Build the invoice snapshot production would know on a historical score date."""
    known = []
    for row in records:
        if row["issue_date"] > score_date:
            continue
        snapshot = dict(row)
        paid_at = _as_date(row.get("outcome_paid_at") or row.get("settled_at"))
        cancelled_at = _as_date(row.get("cancelled_at"))
        partial_at = _as_date(row.get("partial_at"))
        partial_is_known = partial_at is not None and partial_at <= score_date
        snapshot["partial_at"] = partial_at if partial_is_known else None
        snapshot["partial_amount"] = row.get("partial_amount", 0) if partial_is_known else 0
        if paid_at and paid_at <= score_date:
            snapshot["settled_at"] = paid_at
            snapshot["paid"] = row["total"]
            snapshot["outstanding"] = 0
            snapshot["status"] = "PAID"
        else:
            snapshot["settled_at"] = None
            snapshot["paid"] = snapshot["partial_amount"]
            snapshot["outstanding"] = row["total"] - snapshot["paid"]
            snapshot["status"] = "PARTIALLY_PAID" if snapshot["paid"] else "UNPAID"
        snapshot["cancelled_at"] = cancelled_at if cancelled_at and cancelled_at <= score_date else None
        if snapshot["cancelled_at"]:
            snapshot["status"] = "CANCELLED"
            snapshot["outstanding"] = 0
        viewed_at = _as_date(row.get("viewed_at"))
        snapshot["viewed_at"] = viewed_at if viewed_at and viewed_at <= score_date else None
        observed_at = _as_date(row.get("viewed_observed_at"))
        snapshot["viewed_observed_at"] = observed_at if observed_at and observed_at <= score_date else None
        snapshot["viewed_by_recipient"] = bool(
            row.get("viewed_by_recipient") and snapshot["viewed_observed_at"]
        )
        snapshot["reminder_events"] = [
            event for event in row.get("reminder_events", [])
            if (event_date := _as_date(event.get("sent_at") if isinstance(event, dict) else event))
            and event_date <= score_date
        ]
        for key in ("outcome_paid_at", "observation_cutoff", "cancelled"):
            snapshot.pop(key, None)
        known.append(snapshot)
    return known


def _production_curve_at_checkpoint(
    records_by_client: dict[str, list[dict]], invoice: dict, score_date: date,
    model, active_model: str, baseline: list[float], calibration: dict,
) -> list[float]:
    """Replay API scoring from the facts and model outputs known at score_date."""
    visible_records = _records_as_of(records_by_client[invoice["client_id"]], score_date)
    visible_by_id = {row["id"]: row for row in visible_records}
    target = visible_by_id[invoice["id"]]
    history = [
        row for row in visible_records
        if row.get("settled_at") and row["settled_at"] <= score_date
    ]
    age = max(0, (score_date - target["issue_date"]).days)
    base_features = features(target, score_date, history, visible_records)
    use_model = model is not None and (active_model == "cox" or base_features["history_count"] >= 3)
    if use_model and active_model == "cox":
        feature_row = [base_features[name] for name in FEATURES]
        absolute = predict_cox_survival(np.asarray([feature_row], dtype=np.float32), model)[0]
        curve = conditional_survival_from_absolute(absolute, age, HORIZON)
    elif use_model:
        matrix = [
            [features(target, score_date + timedelta(days=offset), history, visible_records)[name] for name in FEATURES]
            for offset in range(HORIZON)
        ]
        hazards = np.asarray(model.predict(np.asarray(matrix, dtype=np.float32), num_threads=1), dtype=float).tolist()
        curve = survival_from_hazards(hazards, HORIZON)
    else:
        hazards = [baseline[age + offset] if age + offset < len(baseline) else 0.0 for offset in range(HORIZON)]
        curve = survival_from_hazards(hazards, HORIZON)
    curve, _, _ = apply_production_overlays(
        curve, target, visible_records, history, score_date, calibration, HORIZON,
    )
    return curve


def _conditional_ipcw_brier_term(
    curve: list[float], event: int, remaining: int, age: int,
    horizon: int, censor_g: list[float],
) -> float | None:
    """Return a conditional IPCW Brier contribution, or None for unobserved outcomes."""
    if not curve or len(curve) <= horizon or not censor_g:
        return None
    estimate = min(1.0, max(0.0, 1.0 - float(curve[horizon])))
    if event == 1 and remaining <= horizon:
        label = 1.0
        censor_time = max(0, age + remaining - 1)
    elif remaining >= horizon:
        label = 0.0
        censor_time = age + horizon
    else:
        return None
    at_checkpoint = max(1e-6, float(censor_g[min(max(0, age), len(censor_g) - 1)]))
    through_outcome = max(1e-6, float(censor_g[min(max(0, censor_time), len(censor_g) - 1)]))
    weight = at_checkpoint / through_outcome
    return weight * (estimate - label) ** 2


def brier_score_at(subjects, predictions: dict[str, list[float]], censor_g: list[float], horizon: int) -> float | None:
    terms = []
    for invoice, duration, event in subjects:
        if duration < horizon and event == 0:
            continue
        surv = predictions.get(invoice["id"])
        if not surv or len(surv) <= horizon:
            continue
        estimate = surv[horizon]
        if event == 1 and duration <= horizon:
            weight = 1.0 / censor_g[max(0, duration - 1)]
            terms.append((estimate ** 2) * weight)
        else:
            weight = 1.0 / censor_g[horizon]
            terms.append(((1.0 - estimate) ** 2) * weight)
    return float(np.mean(terms)) if terms else None


def _paid_only_mae(subjects, prediction_days: dict[str, int | None]) -> float | None:
    errors = [abs(prediction_days[row["id"]] - duration) for row, duration, event in subjects
              if event == 1 and prediction_days.get(row["id"]) is not None]
    return float(np.mean(errors)) if errors else None


def _invoice_feature_matrix(records: list[dict], subjects) -> np.ndarray:
    by_client: dict[str, list[dict]] = {}
    for row in records:
        by_client.setdefault(row["client_id"], []).append(row)
    return np.asarray([
        _row_features(records, invoice, invoice["issue_date"], client_records=by_client[invoice["client_id"]])
        for invoice, _, _ in subjects
    ], dtype=np.float32)


def _integrated_brier(subjects, predictions, censor_g, horizons) -> tuple[float | None, list[float | None]]:
    scores = [brier_score_at(subjects, predictions, censor_g, horizon) for horizon in horizons]
    available = [score for score in scores if score is not None]
    return (float(np.mean(available)) if available else None), scores


def _calibration_rows(subjects, predictions):
    values = []
    for invoice, duration, event in subjects:
        curve = predictions.get(invoice["id"])
        if not curve or len(curve) <= 14 or (not event and duration < 14):
            continue
        values.append((1.0 - curve[14], int(event == 1 and duration <= 14)))
    return values


def _calibration_deciles(values, calibrator=None) -> list[dict]:
    if not values:
        return []
    ordered = sorted(values, key=lambda item: item[0])
    output = []
    for index, group in enumerate(np.array_split(np.asarray(ordered, dtype=float), min(10, len(ordered))), start=1):
        if len(group) == 0:
            continue
        mean_predicted = float(group[:, 0].mean())
        output.append({
            "decile": index, "count": int(len(group)), "predicted": mean_predicted,
            "calibrated": float(calibrator.predict([mean_predicted])[0]) if calibrator else mean_predicted,
            "observed": float(group[:, 1].mean()),
        })
    return output


def _model_metrics(subjects, predictions, censor_g, horizons) -> dict:
    daily_horizons = list(range(1, HORIZON + 1))
    ibs, _ = _integrated_brier(subjects, predictions, censor_g, daily_horizons)
    brier_scores = [brier_score_at(subjects, predictions, censor_g, horizon) for horizon in horizons]
    risks = {}
    for invoice, _, _ in subjects:
        curve = predictions.get(invoice["id"])
        if curve:
            p50_day = next((day for day in range(1, min(HORIZON, len(curve) - 1) + 1) if curve[day] <= 0.5), None)
            risks[invoice["id"]] = float(HORIZON + 1 - p50_day) if p50_day is not None else 0.0
    return {
        "uno_concordance": uno_concordance(subjects, risks, censor_g),
        "integrated_brier_score": ibs,
        "brier_scores": {str(day): score for day, score in zip(horizons, brier_scores, strict=True)},
        "time_dependent_auc": {
            "due_date": time_dependent_auc(subjects, predictions, 0),
            "due_plus_14_days": time_dependent_auc(subjects, predictions, 14),
            "due_plus_30_days": time_dependent_auc(subjects, predictions, 30),
        },
    }


def _new_hazard_model() -> LGBMClassifier:
    return LGBMClassifier(
        objective="binary", n_estimators=180, learning_rate=0.045, num_leaves=15,
        max_depth=-1, min_child_samples=24, reg_lambda=2.0, colsample_bytree=0.9,
        subsample=0.9, random_state=SEED, n_jobs=2, verbosity=-1,
    )


def _ablation_metrics(records, X_train, y_train, train_subjects, test_subjects,
                      test_censor_g, test_horizons, test_predictions) -> list[dict]:
    """Retrain reduced feature sets while keeping invoice-level time splits fixed."""
    definitions = {
        "without_weekday": {
            "features": FEATURES,
            "note": "Weekday is already absent from the deployed v6 schema; this equals the full LightGBM feature set.",
            "reuse_full": True,
        },
        "without_reminder_features": {
            "features": [name for name in FEATURES if name not in {"reminder_count", "days_since_last_reminder"}],
            "note": "Retrained after removing reminder count and recency.",
        },
        "client_history_only": {
            "features": [
                "history_count", "history_late_rate", "recent_delay_days", "delay_std_days",
                "history_missing", "prior_paid_invoice_count",
            ],
            "note": "Retrained using only prior settled-client history features.",
        },
    }
    output = []
    fallback = km_hazards(train_subjects)
    for name, definition in definitions.items():
        feature_names = definition["features"]
        if definition.get("reuse_full"):
            metrics = _model_metrics(test_subjects, test_predictions, test_censor_g, test_horizons)
        else:
            indices = [FEATURES.index(feature) for feature in feature_names]
            model = _new_hazard_model()
            model.fit(X_train[:, indices], y_train, feature_name=feature_names, callbacks=[])
            predictions = {}
            for invoice, _, _ in test_subjects:
                hazards = predict_hazards(
                    model.booster_, records, invoice, fallback, feature_names=feature_names,
                )
                predictions[invoice["id"]] = _survival_from_hazards(hazards)
            metrics = _model_metrics(test_subjects, predictions, test_censor_g, test_horizons)
        output.append({"variant": name, "feature_count": len(feature_names), "note": definition["note"], **metrics})
    return output


def train(output_dir: Path = Path("artifacts"), observation_cutoff: date | str | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    history_cutoff = resolve_as_of(observation_cutoff)
    train_cutoff = history_cutoff - timedelta(days=360)
    validation_lo = train_cutoff + timedelta(days=1)
    validation_hi = history_cutoff - timedelta(days=181)
    validation_cutoff = history_cutoff
    test_lo = history_cutoff - timedelta(days=180)
    test_hi = history_cutoff
    records = generate_records(observation_cutoff=history_cutoff)
    deployment_issue_dates = [
        row["issue_date"] for row in records
        if not row["cancelled"] and row["issue_date"] <= history_cutoff
    ]
    X_train, y_train, train_subjects = make_risk_rows(records, train_cutoff, lambda row: row["issue_date"] <= train_cutoff)
    if len(X_train) == 0 or y_train.sum() == 0:
        raise RuntimeError("Synthetic training split produced no full settlements; refusing to save a nominal model.")
    model = _new_hazard_model()
    model.fit(X_train, y_train, feature_name=FEATURES, callbacks=[])
    booster = model.booster_
    booster.save_model(str(output_dir / "payment_hazard.txt"))
    baseline = km_hazards(train_subjects)
    (output_dir / "kaplan_meier.json").write_text(json.dumps({"hazards": baseline, "horizon_days": HORIZON, "method": "pooled Kaplan–Meier"}, indent=2))

    X_validation, y_validation, val_subjects = make_risk_rows(records, validation_cutoff, lambda r: validation_lo <= r["issue_date"] <= validation_hi)
    _, _, test_subjects = make_risk_rows(records, history_cutoff, lambda r: test_lo <= r["issue_date"] <= test_hi)
    # Each invoice contributes all of its daily rows to exactly one time cohort.
    val_predictions: dict[str, list[float]] = {}
    for row, _, _ in val_subjects:
        hazards = predict_hazards(booster, records, row, baseline)
        val_predictions[row["id"]] = _survival_from_hazards(hazards)
    km_survival = _survival_from_hazards(baseline)
    km_val_predictions = {row["id"]: km_survival for row, _, _ in val_subjects}
    censor_g_val = _censor_survival(train_subjects)
    val_horizons = [h for h in (7, 14, 30, 60, 90, 120, 180) if h <= max((d for _, d, _ in val_subjects), default=0)]
    val_ibs, _ = _integrated_brier(val_subjects, val_predictions, censor_g_val, list(range(1, HORIZON + 1)))
    val_km_ibs, _ = _integrated_brier(val_subjects, km_val_predictions, censor_g_val, list(range(1, HORIZON + 1)))
    train_invoice_X = _invoice_feature_matrix(records, train_subjects)
    cox_model = fit_cox(train_invoice_X, np.asarray([duration for _, duration, _ in train_subjects]),
                        np.asarray([event for _, _, event in train_subjects]), HORIZON)
    cox_val_X = _invoice_feature_matrix(records, val_subjects)
    cox_val_curves = predict_cox_survival(cox_val_X, cox_model)
    cox_val_predictions = {row[0]["id"]: curve for row, curve in zip(val_subjects, cox_val_curves, strict=True)}
    val_cox_ibs, _ = _integrated_brier(val_subjects, cox_val_predictions, censor_g_val, list(range(1, HORIZON + 1)))
    validation_integrated_brier = {"lightgbm": val_ibs, "kaplan_meier": val_km_ibs, "cox": val_cox_ibs}
    eligible_models = [(score, name) for name, score in validation_integrated_brier.items() if score is not None]
    active_model = min(eligible_models)[1] if eligible_models else "kaplan_meier"

    test_predictions: dict[str, list[float]] = {}
    for row, _, _ in test_subjects:
        hazards = predict_hazards(booster, records, row, baseline)
        surv = _survival_from_hazards(hazards)
        test_predictions[row["id"]] = surv
    km_test_predictions = {row["id"]: km_survival for row, _, _ in test_subjects}
    test_censor_g = _censor_survival([s for s in train_subjects + val_subjects])
    test_horizons = [h for h in (7, 14, 30, 60, 90, 120, 180) if h <= max((d for _, d, _ in test_subjects), default=0)]
    cox_test_X = _invoice_feature_matrix(records, test_subjects)
    cox_curves = predict_cox_survival(cox_test_X, cox_model)
    cox_test_predictions = {row[0]["id"]: curve for row, curve in zip(test_subjects, cox_curves, strict=True)}
    test_metrics = {
        "lightgbm": _model_metrics(test_subjects, test_predictions, test_censor_g, test_horizons),
        "kaplan_meier": _model_metrics(test_subjects, km_test_predictions, test_censor_g, test_horizons),
        "cox": _model_metrics(test_subjects, cox_test_predictions, test_censor_g, test_horizons),
    }
    validation_predictions_by_model = {
        "lightgbm": val_predictions, "kaplan_meier": km_val_predictions, "cox": cox_val_predictions,
    }
    active_val_predictions = validation_predictions_by_model[active_model]
    active_test_predictions = {
        "lightgbm": test_predictions, "kaplan_meier": km_test_predictions,
        "cox": cox_test_predictions,
    }[active_model]
    ablation_metrics = _ablation_metrics(
        records, X_train, y_train, train_subjects, test_subjects,
        test_censor_g, test_horizons, test_predictions,
    )

    # Fit an optional isotonic mapping on the validation fold for the 14-day output.
    val_calibration = _calibration_rows(val_subjects, active_val_predictions)
    calibrator = None
    raw_val_brier = calibrated_val_brier = None
    if len(val_calibration) >= 30 and len({round(predicted, 4) for predicted, _ in val_calibration}) >= 5:
        calibration_x = np.asarray([item[0] for item in val_calibration])
        calibration_y = np.asarray([item[1] for item in val_calibration])
        candidate = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        candidate.fit(calibration_x, calibration_y)
        raw_val_brier = float(np.mean((calibration_x - calibration_y) ** 2))
        calibrated_values = candidate.predict(calibration_x)
        calibrated_val_brier = float(np.mean((calibrated_values - calibration_y) ** 2))
        if calibrated_val_brier + 1e-4 < raw_val_brier:
            calibrator = candidate
    calibration_deciles = _calibration_deciles(val_calibration, calibrator)
    calibration_artifact = {
        "applied": calibrator is not None, "model": active_model,
        "target": "P(full settlement within 14 days)",
        "method": "isotonic regression on validation invoices" if calibrator else "identity",
        "thresholds_x": [float(value) for value in calibrator.X_thresholds_] if calibrator else [0.0, 1.0],
        "thresholds_y": [float(value) for value in calibrator.y_thresholds_] if calibrator else [0.0, 1.0],
        "validation_brier_raw": raw_val_brier, "validation_brier_calibrated": calibrated_val_brier,
        "validation_cohort": [validation_lo.isoformat(), validation_hi.isoformat()],
    }
    (output_dir / "calibration.json").write_text(json.dumps(calibration_artifact, indent=2))
    test_calibration = _calibration_rows(test_subjects, active_test_predictions)
    calibration_30d = horizon_calibration(test_subjects, active_test_predictions, test_censor_g, 30)
    test_residual_drift = residual_drift(
        val_subjects, active_val_predictions, test_subjects, active_test_predictions,
    )
    test_p14_raw = [value for value, _ in test_calibration]
    test_p14_labels = [label for _, label in test_calibration]
    test_p14_calibrated = calibrator.predict(test_p14_raw).tolist() if calibrator and test_p14_raw else test_p14_raw
    test_calibration_metrics = {
        "count": len(test_p14_labels), "raw_brier": float(np.mean((np.asarray(test_p14_raw) - np.asarray(test_p14_labels)) ** 2)) if test_p14_labels else None,
        "calibrated_brier": float(np.mean((np.asarray(test_p14_calibrated) - np.asarray(test_p14_labels)) ** 2)) if test_p14_labels else None,
        "deciles": _calibration_deciles(test_calibration, calibrator),
    }

    validation_months = sorted({row["issue_date"].strftime("%Y-%m") for row, _, _ in val_subjects})
    recent_months = set(validation_months[-3:])
    recent_val_subjects = [
        subject for subject in val_subjects
        if subject[0]["issue_date"].strftime("%Y-%m") in recent_months
    ]
    earlier_val_subjects = [
        subject for subject in val_subjects
        if subject[0]["issue_date"].strftime("%Y-%m") not in recent_months
    ]
    within_validation_drift = residual_drift(
        earlier_val_subjects, active_val_predictions, recent_val_subjects, active_val_predictions,
    )
    use_recent_calibration = bool(
        within_validation_drift.get("drift_detected_at_0_05")
        and sum(event for _, _, event in recent_val_subjects) >= 20
    )
    calibration_subjects = recent_val_subjects if use_recent_calibration else val_subjects
    conformal = fit_conformal(calibration_subjects, active_val_predictions)
    conformal["selection"] = {
        "window": "most recent three issue months" if use_recent_calibration else "full validation window",
        "recent_months": sorted(recent_months),
        "recent_paid_count": int(sum(event for _, _, event in recent_val_subjects)),
        "validation_residual_drift": within_validation_drift,
        "rule": "Use the recent window only when earlier-versus-recent validation residual KS p < 0.05 and the recent window has at least 20 paid invoices; never select a window using test outcomes.",
    }
    calibration_artifact["interval_calibration"] = conformal
    (output_dir / "calibration.json").write_text(json.dumps(calibration_artifact, indent=2))

    # Empirical issue-date forecast backtest for paid invoices in the held-out cohort.
    backtest_rows = []
    p50_errors = []
    covered = 0
    eligible_for_coverage = 0
    for invoice, duration, event in test_subjects:
        if not event:
            continue
        curve = active_test_predictions[invoice["id"]]
        raw_p10 = next((day for day, value in enumerate(curve) if day > 0 and value <= 0.9), None)
        p50 = next((day for day, value in enumerate(curve) if day > 0 and value <= 0.5), None)
        raw_p90 = next((day for day, value in enumerate(curve) if day > 0 and value <= 0.1), None)
        interval_lo, _, interval_hi = calibrated_band(
            curve, correction_for_currency(conformal, invoice["currency"]),
        )
        actual = invoice["issue_date"] + timedelta(days=duration)
        predicted = invoice["issue_date"] + timedelta(days=p50) if p50 is not None else None
        error = abs(p50 - duration) if p50 is not None else None
        if error is not None:
            p50_errors.append(error)
        if interval_lo is not None and interval_hi is not None:
            eligible_for_coverage += 1
            covered += int(interval_lo <= duration <= interval_hi)
        backtest_rows.append({
            "client_id": invoice["client_id"], "currency": invoice["currency"],
            "invoice_id": invoice["id"], "issue_date": invoice["issue_date"].isoformat(),
            "predicted_p50_date": predicted.isoformat() if predicted else None,
            "actual_paid_date": actual.isoformat(),
            "p10_date": (invoice["issue_date"] + timedelta(days=raw_p10)).isoformat() if raw_p10 else None,
            "p90_date": (invoice["issue_date"] + timedelta(days=raw_p90)).isoformat() if raw_p90 else None,
            "coverage_interval_lower_date": (invoice["issue_date"] + timedelta(days=interval_lo)).isoformat() if interval_lo else None,
            "coverage_interval_upper_date": (invoice["issue_date"] + timedelta(days=interval_hi)).isoformat() if interval_hi else None,
            "absolute_error_days": error,
            "within_interval": bool(interval_lo is not None and interval_hi is not None and interval_lo <= duration <= interval_hi),
            "within_p10_p90": bool(raw_p10 is not None and raw_p90 is not None and raw_p10 <= duration <= raw_p90),
        })
    coverage = covered / eligible_for_coverage if eligible_for_coverage else None
    monthly_coverage = coverage_by_issue_month(backtest_rows)

    # Replay the conditional production path from the point-in-time invoice
    # snapshot at each checkpoint, including the same shared scoring overlays.
    records_by_client: dict[str, list[dict]] = {}
    for record in records:
        records_by_client.setdefault(record["client_id"], []).append(record)
    deployed_p50_errors: list[float] = []
    deployed_brier_sums: dict[int, float] = {14: 0.0, 30: 0.0, 60: 0.0}
    deployed_brier_counts: dict[int, int] = {14: 0, 30: 0, 60: 0}
    deployed_brier_censored: dict[int, int] = {14: 0, 30: 0, 60: 0}
    deployed_observation_count = 0
    replay_model = booster if active_model == "lightgbm" else cox_model if active_model == "cox" else None
    for invoice, duration, event in test_subjects:
        terms = max(0, (invoice["due_date"] - invoice["issue_date"]).days)
        for overdue_checkpoint in (1, 30, 90):
            age = terms + overdue_checkpoint
            if age >= duration or age >= HORIZON:
                continue
            score_date = invoice["issue_date"] + timedelta(days=age)
            curve = _production_curve_at_checkpoint(
                records_by_client, invoice, score_date, replay_model, active_model,
                baseline, calibration_artifact,
            )
            deployed_observation_count += 1
            remaining = duration - age
            p50_day = next((day for day, survival in enumerate(curve) if day > 0 and survival <= 0.5), None)
            if event and p50_day is not None:
                deployed_p50_errors.append(abs(p50_day - remaining))
            for horizon in deployed_brier_sums:
                deployed_brier_counts[horizon] += 1
                contribution = _conditional_ipcw_brier_term(
                    curve, event, remaining, age, horizon, test_censor_g,
                )
                if contribution is None:
                    deployed_brier_censored[horizon] += 1
                else:
                    deployed_brier_sums[horizon] += contribution
    deployed_forecast_evaluation = {
        "model": active_model,
        "scoring_path": "production model scoring from point-in-time invoice and client history at each checkpoint, conditional survival, client-history tail blend, validation-fitted 14-day calibration, mature overdue recovery ceiling, and promise scenario overlay",
        "overdue_checkpoints_days": [1, 30, 90],
        "observation_count": deployed_observation_count,
        "paid_only_p50_median_absolute_error_days": float(np.median(deployed_p50_errors)) if deployed_p50_errors else None,
        "paid_only_p50_count": len(deployed_p50_errors),
        "brier_by_horizon": {
            f"{horizon}d": deployed_brier_sums[horizon] / deployed_brier_counts[horizon]
            if deployed_brier_counts[horizon] else None
            for horizon in deployed_brier_sums
        },
        "brier_observation_counts": {f"{horizon}d": count for horizon, count in deployed_brier_counts.items()},
        "brier_censored_before_horizon_excluded_counts": {
            f"{horizon}d": count for horizon, count in deployed_brier_censored.items()
        },
        "brier_method": "Conditional inverse-probability-of-censoring-weighted Brier score; known payments within a horizon are positive outcomes, invoices observed through the horizon are negative outcomes, and earlier censoring contributes zero over the at-risk denominator.",
        "limitations": "Synthetic held-out replay at due-date +1/+30/+90 days using each checkpoint's observable facts. Conditional horizons beyond the original 180-day observation window are right-censored. The synthetic cohort has no observed promise-adherence outcomes, so its illustrative promise overlay receives no independent adherence validation.",
    }

    # Decile plot uses held-out 14-day probabilities and the validation-trained mapping.
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=150)
    if test_calibration_metrics["deciles"]:
        points = test_calibration_metrics["deciles"]
        ax.plot([point["predicted"] for point in points], [point["observed"] for point in points], marker="o", label="Raw predicted")
        ax.plot([point["calibrated"] for point in points], [point["observed"] for point in points], marker="s", label="Calibrated")
    ax.plot([0, 1], [0, 1], color="#888888", linestyle="--", label="Ideal")
    ax.set(xlabel="Predicted P(full payment within 14 days)", ylabel="Observed share paid within 14 days", xlim=(0, 1), ylim=(0, 1), title="Synthetic held-out 14-day calibration")
    ax.grid(alpha=0.22)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output_dir / "calibration.png")
    plt.close(fig)

    model_name = {"lightgbm": "lgbm-hazard", "kaplan_meier": "kaplan-meier", "cox": "cox-ph"}[active_model]
    model_version = f"{model_name}-v7-seed-{SEED}-asof-{history_cutoff.isoformat()}"
    baseline_model_version = f"kaplan-meier-v7-seed-{SEED}-asof-{history_cutoff.isoformat()}"
    for row in backtest_rows:
        row["model_version"] = model_version
    if len(X_validation) and len(y_validation):
        permutation = permutation_importance(
            model, X_validation, y_validation, scoring="neg_log_loss", n_repeats=3,
            random_state=SEED, n_jobs=2,
        )
        feature_importance = sorted(
            ({"feature": name, "importance": float(value), "std": float(spread)}
             for name, value, spread in zip(FEATURES, permutation.importances_mean, permutation.importances_std, strict=True)),
            key=lambda item: item["importance"], reverse=True,
        )
    else:
        feature_importance = []
    (output_dir / "feature_importance.json").write_text(json.dumps({
        "model_version": model_version, "method": "permutation importance on validation risk rows (negative log loss)",
        "features": feature_importance,
    }, indent=2))
    shap_rows = X_validation[:2000]
    shap_values = booster.predict(shap_rows, pred_contrib=True, num_threads=1) if len(shap_rows) else np.empty((0, len(FEATURES)+1))
    shap_summary = sorted([{"feature": name, "mean_absolute_shap": float(np.abs(shap_values[:, index]).mean())}
                           for index, name in enumerate(FEATURES)] if len(shap_rows) else [], key=lambda row: -row["mean_absolute_shap"])
    (output_dir / "shap_summary.json").write_text(json.dumps({"count": len(shap_rows), "method": "TreeSHAP contributions on validation risk rows", "features": shap_summary}, indent=2))
    confidence_intervals = client_bootstrap(
        test_subjects,
        {"lightgbm": test_predictions, "kaplan_meier": km_test_predictions, "cox": cox_test_predictions},
        test_censor_g,
    )
    age_errors = []
    for row, duration, event in test_subjects:
        if not event:
            continue
        terms = (row["due_date"] - row["issue_date"]).days
        for label, age in [("not_due", max(0, terms-1)), ("1–30", terms+1), ("31–89", terms+31), ("90+", terms+90)]:
            if age >= duration or age >= HORIZON:
                continue
            curve = active_test_predictions[row["id"]]
            denominator = max(1e-12, curve[age])
            conditional = [min(1, value/denominator) for value in curve[age:]]
            conditional += [conditional[-1]] * (HORIZON+1-len(conditional))
            overdue_at_observation = max(0, age - terms)
            age_widening = int(np.ceil(max(0, overdue_at_observation - 60) / 6))
            lo, med, hi = calibrated_band(
                conditional, correction_for_currency(conformal, row["currency"]) + age_widening,
            )
            age_errors.append({"age_bucket": label, "absolute_error_days": abs(age+med-duration) if med else None, "within_p10_p90": bool(lo and hi and lo <= duration-age <= hi)})
    training_issue_dates = [row["issue_date"] for row, _, _ in train_subjects]
    schema = {
        "version": FEATURE_SCHEMA_VERSION, "features": FEATURES, "horizon_days": HORIZON, "seed": SEED,
        "dataset": "fully synthetic invoice records generated locally; 52 clients; no downloads",
        "as_of_date": history_cutoff.isoformat(), "history_cutoff": history_cutoff.isoformat(),
        "training_cutoff": train_cutoff.isoformat(),
        "training_date_range": [min(training_issue_dates).isoformat(), max(training_issue_dates).isoformat()],
        "validation_cohort": [validation_lo.isoformat(), validation_hi.isoformat()], "validation_observation_cutoff": validation_cutoff.isoformat(),
        "test_cohort": [test_lo.isoformat(), test_hi.isoformat()], "test_observation_cutoff": history_cutoff.isoformat(),
        "validation_unit": "invoice; all daily risk rows for an invoice stay in the same time cohort",
        "feature_priors": {"delay_days": GLOBAL_DELAY_PRIOR_DAYS, "late_rate": GLOBAL_LATE_RATE_PRIOR,
                           "amount_median": GLOBAL_AMOUNT_MEDIAN_PRIOR, "history_prior_strength": HISTORY_PRIOR_STRENGTH},
        "generation_parameters": {"client_profiles": ["prompt", "due_date", "chronic_late", "deteriorating", "erratic"], "invoice_count": len(records), "clients": len({row["client_id"] for row in records}), "horizon_days": HORIZON},
    }
    active_test_metrics = test_metrics[active_model]
    comparison_specs = {
        "uno_concordance": "higher",
        "integrated_brier_score": "lower",
    }
    wins, losses = [], []
    for metric_name, direction in comparison_specs.items():
        active_value = active_test_metrics.get(metric_name)
        if active_value is None:
            continue
        for comparator_name, comparator in test_metrics.items():
            if comparator_name == active_model or comparator.get(metric_name) is None:
                continue
            better = active_value > comparator[metric_name] if direction == "higher" else active_value < comparator[metric_name]
            (wins if better else losses).append(f"{metric_name} vs {comparator_name}")
    active_due_auc = (active_test_metrics.get("time_dependent_auc") or {}).get("due_date")
    for comparator_name, comparator in test_metrics.items():
        if comparator_name == active_model:
            continue
        comparator_auc = (comparator.get("time_dependent_auc") or {}).get("due_date")
        if active_due_auc is not None and comparator_auc is not None:
            (wins if active_due_auc > comparator_auc else losses).append(f"due-date AUC vs {comparator_name}")
    model_card = {
        "deployed_model": active_model,
        "selection_rule": "Lowest inverse-probability-of-censoring-weighted integrated Brier score on the time-separated validation cohort; candidates are LightGBM, pooled Kaplan–Meier, and Cox PH.",
        "validation_integrated_brier": validation_integrated_brier,
        "held_out_metrics_won": wins,
        "held_out_metrics_lost": losses,
        "training_window": [min(deployment_issue_dates).isoformat(), max(deployment_issue_dates).isoformat()],
        "training_cutoff": history_cutoff.isoformat(),
        "known_limits": [
            "Synthetic-only training cohort; external public-data evaluation is reported separately.",
            "Paid-only date error and interval coverage exclude invoices censored before final payment; IPCW survival scores include observed censored records.",
            "The final artifact is refit through the current cutoff after evaluation; validation calibration is transferred and should be monitored.",
        ],
    }
    evaluation = {
        "as_of_date": history_cutoff.isoformat(), "seed": SEED, "model_version": model_version,
        "split": {
            "train_cutoff": train_cutoff.isoformat(), "training_date_range": schema["training_date_range"],
            "validation_cohort": [validation_lo.isoformat(), validation_hi.isoformat()],
            "validation_observation_cutoff": validation_cutoff.isoformat(),
            "test_cohort": [test_lo.isoformat(), test_hi.isoformat()],
            "test_observation_cutoff": history_cutoff.isoformat(), "unit": "invoice",
        },
        "policy_simulation": replay(test_subjects),
        "survival_metrics": test_metrics,
        "concordance_confidence_intervals": confidence_intervals,
        "model_comparison_note": (
            f"Release model selected: {active_model}. Selection uses validation integrated Brier across LightGBM ({val_ibs:.3f}), pooled Kaplan–Meier ({val_km_ibs:.3f}), and Cox PH ({val_cox_ibs:.3f}); the held-out cohort is not used for model selection."
            if all(value is not None for value in (val_ibs, val_km_ibs, val_cox_ibs)) else
            f"Release model selected: {active_model}. Validation integrated Brier estimates were unavailable, so no measured superiority claim is supported. Issue weekday features are absent from the v6 schema."
        ),
        "model_card": model_card,
        "deployed_forecast_evaluation": deployed_forecast_evaluation,
        "ablation_metrics": ablation_metrics,
        "shap_summary": shap_summary,
        "interval_calibration": conformal,
        "error_by_currency": grouped_errors(backtest_rows, "currency", "within_interval"),
        "error_by_age_bucket": grouped_errors(age_errors, "age_bucket", "within_p10_p90"),
        "drift_backtest": backtest_drift(records, history_cutoff),
        "censor_aware_evaluation": {
            "test_invoice_count": len(test_subjects),
            "paid_event_count": int(sum(event for _, _, event in test_subjects)),
            "right_censored_count": int(sum(not event for _, _, event in test_subjects)),
            "metrics": "Uno concordance, time-dependent AUC, and integrated Brier use right-censoring-aware estimators; paid-only date errors remain descriptive.",
        },
        "calibration_30d": calibration_30d,
        "residual_drift": test_residual_drift,
        "coverage_by_issue_month": monthly_coverage,
        "calibration_14d": test_calibration_metrics | {"count_unit": "held-out invoice-probability observations", "validation_deciles": calibration_deciles},
        "backtest": {
            "paid_invoice_count": len(backtest_rows), "p50_median_absolute_error_days": float(np.median(p50_errors)) if p50_errors else None,
            "p10_p90_coverage": coverage, "coverage_invoice_count": eligible_for_coverage,
            "coverage_hit_count": covered,
            "target_coverage": 0.8, "invoices": backtest_rows,
            "interval_label": "Validation-calibrated coverage interval; P10/P50/P90 dates separately come from the displayed survival curve.",
            "raw_quantile_coverage": sum(row["within_p10_p90"] for row in backtest_rows) / len(backtest_rows) if backtest_rows else None,
        },
        "cox_baseline_fit": {"converged": cox_model["converged"], "iterations": cox_model["iterations"]},
        "caveat": "These synthetic-cohort metrics verify the pipeline only; they do not establish real-world forecast accuracy.",
    }
    (output_dir / "eval.json").write_text(json.dumps(evaluation, indent=2))
    report = {
        "model_version": model_version, "baseline_model_version": baseline_model_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "seed": SEED, "training_seed": SEED, "feature_list": FEATURES,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "as_of_date": history_cutoff.isoformat(), "history_cutoff": history_cutoff.isoformat(),
        "training_date_range": schema["training_date_range"],
        "training_cutoff": train_cutoff.isoformat(),
        "train_rows": int(len(X_train)), "train_event_rows": int(y_train.sum()), "train_invoices": len(train_subjects),
        "validation_invoices": len(val_subjects), "validation_event_count": int(sum(event for _, _, event in val_subjects)),
        "validation_horizons": val_horizons, "validation_ibs_lightgbm": val_ibs,
        "validation_ibs_km": val_km_ibs, "validation_ibs_cox": val_cox_ibs,
        "active_model": active_model,
        "kaplan_meier_model_version": baseline_model_version,
        "cox_model_version": f"cox-ph-v7-seed-{SEED}-asof-{history_cutoff.isoformat()}",
        "validation_cohort": [validation_lo.isoformat(), validation_hi.isoformat()],
        "validation_observation_cutoff": validation_cutoff.isoformat(),
        "test_cohort": [test_lo.isoformat(), test_hi.isoformat()], "test_observation_cutoff": history_cutoff.isoformat(),
        "test_invoices": len(test_subjects), "test_event_count": int(sum(event for _, _, event in test_subjects)),
        "test_horizons": test_horizons,
        "test_integrated_brier_lightgbm": test_metrics["lightgbm"]["integrated_brier_score"],
        "test_integrated_brier_km": test_metrics["kaplan_meier"]["integrated_brier_score"],
        "test_uno_concordance_lightgbm": test_metrics["lightgbm"]["uno_concordance"],
        "test_uno_concordance_km": test_metrics["kaplan_meier"]["uno_concordance"],
        "test_uno_concordance_cox": test_metrics["cox"]["uno_concordance"],
        "test_paid_only_date_mae_days": float(np.mean(p50_errors)) if p50_errors else None,
        "test_paid_only_invoice_count": int(sum(event for _, _, event in test_subjects)),
        "test_p10_p90_coverage": coverage,
        "test_calibrated_interval_coverage": coverage,
        "caveat": "These synthetic-cohort metrics verify the pipeline only; they do not establish real-world forecast accuracy. Paid-only date error excludes censored invoices.",
    }
    X_full, y_full, full_subjects = make_risk_rows(records, history_cutoff, lambda row: row["issue_date"] <= history_cutoff)
    model.fit(X_full, y_full, feature_name=FEATURES, callbacks=[])
    model.booster_.save_model(str(output_dir / "payment_hazard.txt"))
    full_invoice_X = _invoice_feature_matrix(records, full_subjects)
    full_cox_model = fit_cox(
        full_invoice_X,
        np.asarray([duration for _, duration, _ in full_subjects]),
        np.asarray([event for _, _, event in full_subjects]),
        HORIZON,
    )
    (output_dir / "cox_model.json").write_text(json.dumps({
        "mean": np.asarray(full_cox_model["mean"]).tolist(),
        "scale": np.asarray(full_cox_model["scale"]).tolist(),
        "beta": np.asarray(full_cox_model["beta"]).tolist(),
        "baseline_cumulative_hazard": np.asarray(full_cox_model["baseline_cumulative_hazard"]).tolist(),
        "converged": full_cox_model["converged"], "iterations": full_cox_model["iterations"],
        "horizon_days": HORIZON, "method": "Cox proportional hazards with Breslow baseline",
    }, indent=2))
    full_baseline = km_hazards(full_subjects)
    (output_dir / "kaplan_meier.json").write_text(json.dumps({"hazards": full_baseline, "horizon_days":HORIZON, "method":"pooled Kaplan–Meier, full window"}, indent=2))
    report["evaluation_training_cutoff"] = report["training_cutoff"]
    report["training_cutoff"] = history_cutoff.isoformat()
    report["training_date_range"] = [min(row["issue_date"] for row, _, _ in full_subjects).isoformat(), history_cutoff.isoformat()]
    report["deployment_refit"] = "LightGBM, Cox PH, and Kaplan–Meier artifacts are refit through as-of; held-out metrics use frozen time-separated fits. The selected artifact is named in active_model and model_version. Validation calibration is transferred and requires monitoring after refit."
    schema["training_cutoff"] = history_cutoff.isoformat()
    schema["training_date_range"] = report["training_date_range"]
    (output_dir / "feature_schema.json").write_text(json.dumps(schema, indent=2))
    (output_dir / "model_metadata.json").write_text(json.dumps(report, indent=2))
    (output_dir / "eval.json").write_text(json.dumps(evaluation, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))
