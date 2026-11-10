from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from lightgbm import LGBMClassifier, Booster

from ml.features import FEATURES, FEATURE_SCHEMA_VERSION, features
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
    values = features(invoice, as_of, history, all_rows)
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
    result = [1.0]
    survival = 1.0
    for hazard in hazards[:HORIZON]:
        survival *= 1.0 - min(1.0, max(0.0, hazard))
        result.append(float(survival))
    return result + [result[-1]] * (HORIZON + 1 - len(result))


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


def predict_hazards(model: Booster, records: list[dict], invoice: dict, hazards_fallback: list[float], issue_origin: bool = True) -> list[float]:
    origin = invoice["issue_date"]
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
    matrix = []
    for day_number in range(1, HORIZON + 1):
        as_of = origin + timedelta(days=day_number - 1)
        matrix.append(_row_features(records, invoice, as_of, history, exposure, client_records))
    raw = np.asarray(matrix, dtype=np.float32)
    if model is None:
        hazards = hazards_fallback.copy()
    else:
        hazards = np.asarray(model.predict(raw), dtype=float).tolist()
    return [min(1.0, max(0.0, float(hazard))) for hazard in hazards]


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
    X_train, y_train, train_subjects = make_risk_rows(records, train_cutoff, lambda row: row["issue_date"] <= train_cutoff)
    if len(X_train) == 0 or y_train.sum() == 0:
        raise RuntimeError("Synthetic training split produced no full settlements; refusing to save a nominal model.")
    model = LGBMClassifier(
        objective="binary", n_estimators=180, learning_rate=0.045, num_leaves=15,
        max_depth=-1, min_child_samples=24, reg_lambda=2.0, colsample_bytree=0.9,
        subsample=0.9, random_state=SEED, n_jobs=2, verbosity=-1,
    )
    model.fit(X_train, y_train, feature_name=FEATURES, callbacks=[])
    booster = model.booster_
    booster.save_model(str(output_dir / "payment_hazard.txt"))
    baseline = km_hazards(train_subjects)
    (output_dir / "kaplan_meier.json").write_text(json.dumps({"hazards": baseline, "horizon_days": HORIZON, "method": "pooled Kaplan–Meier"}, indent=2))

    _, _, val_subjects = make_risk_rows(records, validation_cutoff, lambda r: validation_lo <= r["issue_date"] <= validation_hi)
    _, _, test_subjects = make_risk_rows(records, history_cutoff, lambda r: test_lo <= r["issue_date"] <= test_hi)
    # Validation is scored on its later observation boundary; model selection is reserved for validation.
    val_predictions: dict[str, list[float]] = {}
    val_days: dict[str, int | None] = {}
    for row, _, _ in val_subjects:
        hazards = predict_hazards(booster, records, row, baseline)
        survival = _survival_from_hazards(hazards)
        val_predictions[row["id"]] = survival
        val_days[row["id"]] = next((day for day in range(1, HORIZON + 1) if survival[day] <= 0.5), None)
    # Training survival curve uses the training-cohort KM estimate for the same origin-time baseline.
    km_survival = _survival_from_hazards(baseline)
    km_val_predictions = {row["id"]: km_survival for row, _, _ in val_subjects}
    censor_g_val = _censor_survival(train_subjects)
    val_horizons = [h for h in (7, 14, 30, 60, 90, 120, 180) if h <= max((d for _, d, _ in val_subjects), default=0)]
    val_scores = [brier_score_at(val_subjects, val_predictions, censor_g_val, h) for h in val_horizons]
    val_km_scores = [brier_score_at(val_subjects, km_val_predictions, censor_g_val, h) for h in val_horizons]
    val_ibs = float(np.mean([v for v in val_scores if v is not None])) if any(v is not None for v in val_scores) else None
    val_km_ibs = float(np.mean([v for v in val_km_scores if v is not None])) if any(v is not None for v in val_km_scores) else None
    active_model = "lightgbm" if val_ibs is not None and (val_km_ibs is None or val_ibs <= val_km_ibs) else "kaplan_meier"

    test_predictions: dict[str, list[float]] = {}
    test_days: dict[str, int | None] = {}
    for row, _, _ in test_subjects:
        hazards = predict_hazards(booster, records, row, baseline)
        surv = _survival_from_hazards(hazards)
        test_predictions[row["id"]] = surv
        test_days[row["id"]] = next((day for day in range(1, HORIZON + 1) if surv[day] <= 0.5), None)
    km_test_predictions = {row["id"]: km_survival for row, _, _ in test_subjects}
    test_censor_g = _censor_survival([s for s in train_subjects + val_subjects])
    test_horizons = [h for h in (7, 14, 30, 60, 90, 120, 180) if h <= max((d for _, d, _ in test_subjects), default=0)]
    test_scores = [brier_score_at(test_subjects, test_predictions, test_censor_g, h) for h in test_horizons]
    test_km_scores = [brier_score_at(test_subjects, km_test_predictions, test_censor_g, h) for h in test_horizons]
    test_ibs = float(np.mean([v for v in test_scores if v is not None])) if any(v is not None for v in test_scores) else None
    test_km_ibs = float(np.mean([v for v in test_km_scores if v is not None])) if any(v is not None for v in test_km_scores) else None

    # Calibration plot: predicted and observed payment-by-horizon proportions on the held-out test cohort.
    calibration_days = [7, 14, 30, 60, 90]
    predicted = [float(np.mean([1 - test_predictions[row["id"]][d] for row, _, _ in test_subjects])) for d in calibration_days]
    observed = [float(np.mean([int(event == 1 and duration <= d) for _, duration, event in test_subjects])) for d in calibration_days]
    fig, ax = plt.subplots(figsize=(6.4, 4.2), dpi=150)
    ax.plot(calibration_days, predicted, marker="o", label="LightGBM")
    ax.plot(calibration_days, observed, marker="s", label="Observed test cohort")
    ax.set(xlabel="Days after invoice issue", ylabel="Cumulative probability of full payment", ylim=(0, 1), title="Synthetic held-out calibration")
    ax.grid(alpha=0.22)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(output_dir / "calibration.png")
    plt.close(fig)

    model_version = f"lgbm-hazard-v2-seed-{SEED}-asof-{history_cutoff.isoformat()}"
    schema = {
        "version": FEATURE_SCHEMA_VERSION, "features": FEATURES, "horizon_days": HORIZON, "seed": SEED,
        "dataset": "fully synthetic invoice records generated locally; 52 clients; no downloads",
        "as_of_date": history_cutoff.isoformat(), "history_cutoff": history_cutoff.isoformat(),
        "training_cutoff": train_cutoff.isoformat(),
        "validation_cohort": [validation_lo.isoformat(), validation_hi.isoformat()], "validation_observation_cutoff": validation_cutoff.isoformat(),
        "test_cohort": [test_lo.isoformat(), test_hi.isoformat()], "test_observation_cutoff": history_cutoff.isoformat(),
        "generation_parameters": {"client_profiles": ["prompt", "due_date", "chronic_late", "deteriorating", "erratic"], "invoice_count": len(records), "clients": len({row["client_id"] for row in records}), "horizon_days": HORIZON},
    }
    report = {
        "model_version": model_version, "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "seed": SEED, "as_of_date": history_cutoff.isoformat(), "history_cutoff": history_cutoff.isoformat(),
        "training_cutoff": train_cutoff.isoformat(),
        "train_rows": int(len(X_train)), "train_event_rows": int(y_train.sum()), "train_invoices": len(train_subjects),
        "validation_invoices": len(val_subjects), "validation_event_count": int(sum(event for _, _, event in val_subjects)),
        "validation_horizons": val_horizons, "validation_ibs_lightgbm": val_ibs, "validation_ibs_km": val_km_ibs,
        "active_model": active_model,
        "validation_cohort": [validation_lo.isoformat(), validation_hi.isoformat()],
        "validation_observation_cutoff": validation_cutoff.isoformat(),
        "test_cohort": [test_lo.isoformat(), test_hi.isoformat()], "test_observation_cutoff": history_cutoff.isoformat(),
        "test_invoices": len(test_subjects), "test_event_count": int(sum(event for _, _, event in test_subjects)),
        "test_horizons": test_horizons, "test_ibs_lightgbm": test_ibs, "test_ibs_km": test_km_ibs,
        "test_paid_only_date_mae_days": _paid_only_mae(test_subjects, test_days),
        "test_paid_only_invoice_count": int(sum(event for _, _, event in test_subjects)),
        "caveat": "These synthetic-cohort metrics verify the pipeline only; they do not establish real-world forecast accuracy. Paid-only date MAE excludes censored invoices.",
    }
    (output_dir / "feature_schema.json").write_text(json.dumps(schema, indent=2))
    (output_dir / "model_metadata.json").write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    print(json.dumps(train(), indent=2))
