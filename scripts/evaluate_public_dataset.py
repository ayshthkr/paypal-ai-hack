"""Evaluate the survival pipeline on the public Kaggle Customer Invoices Dataset.

The source file is intentionally not checked into the repository. This script
accepts a local CSV path and writes aggregate metrics only; it never exports
customer names, raw invoice IDs, or source rows.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from ml.train import (
    HORIZON,
    _new_hazard_model,
    _duration,
    _invoice_feature_matrix,
    _model_metrics,
    _survival_from_hazards,
    _censor_survival,
    km_hazards,
    make_risk_rows,
    predict_hazards,
)
from ml.features import FEATURES
from ml.survival_metrics import fit_cox, predict_cox_survival


DATASET = "Customer Invoices Dataset"
DATASET_URL = "https://www.kaggle.com/datasets/pradumn203/payment-date-prediction-for-invoices-dataset"
LICENSE = "CC BY-NC 4.0"
MAX_BENCHMARK_INVOICES = 5_000
MAX_LIGHTGBM_INVOICES = 1_000


def _date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text or text.casefold() in {"nan", "none", "null"}:
        return None
    try:
        if len(text) >= 10 and text[4] == "-":
            return date.fromisoformat(text[:10])
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 8:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None
    return None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def load_public_records(path: Path) -> tuple[list[dict], dict]:
    unique: dict[str, tuple[tuple, dict]] = {}
    conflicting_ids: set[str] = set()
    invalid = 0
    source_rows = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"invoice_id", "cust_number", "posting_date", "due_in_date", "clear_date", "total_open_amount", "invoice_currency", "isOpen"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Input CSV is missing required fields: {', '.join(sorted(missing))}")
        for source in reader:
            source_rows += 1
            raw_id = (source.get("invoice_id") or "").strip()
            issued = _date(source.get("posting_date"))
            due = _date(source.get("due_in_date"))
            paid = _date(source.get("clear_date"))
            try:
                amount = float(source.get("total_open_amount") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            currency = (source.get("invoice_currency") or "").strip().upper()
            client = (source.get("business_code") or "") + ":" + (source.get("cust_number") or "")
            if not raw_id or not issued or not due or not math.isfinite(amount) or amount <= 0 or due < issued:
                invalid += 1
                continue
            is_open = str(source.get("isOpen") or "").strip() == "1" or paid is None
            if not is_open and paid is None:
                invalid += 1
                continue
            signature = (issued, due, paid, round(amount, 2), currency, is_open)
            record = {
                "id": _hash("invoice:" + raw_id),
                "client_id": _hash("client:" + client),
                "issue_date": issued,
                "due_date": due,
                "total": amount,
                "paid": 0.0 if is_open else amount,
                "outstanding": amount if is_open else 0.0,
                "currency": currency,
                "status": "SENT" if is_open else "PAID",
                "settled_at": None if is_open else paid,
                "paid_at": None if is_open else paid,
                "outcome_paid_at": None if is_open else paid,
                "cancelled": False,
                "cancelled_at": None,
                "partial_at": None,
                "partial_amount": 0.0,
                "payment_term_type": (source.get("cust_payment_terms") or "").strip(),
                "partial_payment_allowed": False,
                "reminder_events": [],
            }
            previous = unique.get(raw_id)
            if previous and previous[0] != signature:
                conflicting_ids.add(raw_id)
            elif not previous:
                unique[raw_id] = (signature, record)

    rows = [record for raw_id, (_, record) in unique.items() if raw_id not in conflicting_ids]
    rows.sort(key=lambda row: (row["issue_date"], row["id"]))
    if len(rows) < 100:
        raise ValueError(f"Only {len(rows)} valid, unique invoices remain after cleaning.")
    observation_end = max(
        max(row["issue_date"] for row in rows),
        max((row["outcome_paid_at"] for row in rows if row["outcome_paid_at"]), default=date.min),
    )
    metadata = {
        "source_rows": source_rows,
        "valid_unique_invoice_count": len(rows),
        "conflicting_invoice_id_groups_dropped": len(conflicting_ids),
        "invalid_or_anomalous_rows_dropped": invalid,
        "right_censored_open_count": sum(row["outcome_paid_at"] is None for row in rows),
        "fully_paid_count": sum(row["outcome_paid_at"] is not None for row in rows),
        "observation_end": observation_end.isoformat(),
        "currencies": {currency: sum(row["currency"] == currency for row in rows) for currency in sorted({row["currency"] for row in rows})},
        "issue_date_range": [rows[0]["issue_date"].isoformat(), rows[-1]["issue_date"].isoformat()],
        "privacy": "Customer and invoice identifiers are SHA-256 hashed in memory; output contains aggregates and no row-level data.",
    }
    return rows, metadata


def evaluate(path: Path) -> dict:
    records, cleaning = load_public_records(path)
    observation_end = date.fromisoformat(cleaning["observation_end"])
    split_date = observation_end - timedelta(days=HORIZON)
    train_pool = [row for row in records if row["issue_date"] <= split_date]
    test_pool = [row for row in records if row["issue_date"] > split_date]
    sample_size = min(MAX_BENCHMARK_INVOICES, len(records))
    train_sample_size = min(len(train_pool), round(sample_size * len(train_pool) / len(records)))
    test_sample_size = min(len(test_pool), sample_size - train_sample_size)

    def stable_sample(pool: list[dict], size: int) -> set[str]:
        ordered = sorted(pool, key=lambda row: hashlib.sha256(row["id"].encode()).digest())
        return {row["id"] for row in ordered[:size]}

    train_ids = stable_sample(train_pool, train_sample_size)
    test_ids = stable_sample(test_pool, test_sample_size)

    train_subjects = [
        (row, *_duration(row, observation_end)) for row in records if row["id"] in train_ids
    ]
    test_subjects = [
        (row, *_duration(row, observation_end)) for row in records if row["id"] in test_ids
    ]
    if not any(event for _, _, event in train_subjects) or not test_subjects:
        raise ValueError("The temporal public-data split did not contain train events and test invoices.")

    baseline = km_hazards(train_subjects)
    km_curve = _survival_from_hazards(baseline)
    km_curves = {invoice["id"]: km_curve for invoice, _, _ in test_subjects}

    train_X = _invoice_feature_matrix(records, train_subjects)
    cox = fit_cox(
        train_X,
        np.asarray([duration for _, duration, _ in train_subjects]),
        np.asarray([event for _, _, event in train_subjects]),
        HORIZON,
    )
    test_X = _invoice_feature_matrix(records, test_subjects)
    cox_values = predict_cox_survival(test_X, cox)
    cox_curves = {invoice["id"]: curve for (invoice, _, _), curve in zip(test_subjects, cox_values, strict=True)}
    censor_g = _censor_survival(train_subjects)
    horizons = [day for day in (7, 14, 30, 60, 90, 120, 180) if day <= max(duration for _, duration, _ in test_subjects)]

    # Run the deployed daily-hazard training recipe on a smaller, deterministic
    # sample. The full 5,000-invoice benchmark remains available to the Cox/KM
    # comparators, while this bound keeps daily risk expansion practical.
    lightgbm_size = min(MAX_LIGHTGBM_INVOICES, len(records))
    lightgbm_train_size = min(len(train_pool), round(lightgbm_size * len(train_pool) / len(records)))
    lightgbm_test_size = min(len(test_pool), lightgbm_size - lightgbm_train_size)
    lightgbm_train_ids = stable_sample(train_pool, lightgbm_train_size)
    lightgbm_test_ids = stable_sample(test_pool, lightgbm_test_size)
    lightgbm_train_subjects = [
        (row, *_duration(row, observation_end)) for row in records if row["id"] in lightgbm_train_ids
    ]
    lightgbm_test_subjects = [
        (row, *_duration(row, observation_end)) for row in records if row["id"] in lightgbm_test_ids
    ]
    X_hazard, y_hazard, _ = make_risk_rows(
        records, observation_end, lambda row: row["id"] in lightgbm_train_ids,
    )
    hazard_model = _new_hazard_model()
    hazard_model.fit(X_hazard, y_hazard, feature_name=FEATURES, callbacks=[])
    by_client: dict[str, list[dict]] = {}
    for row in records:
        by_client.setdefault(row["client_id"], []).append(row)
    hazard_baseline = km_hazards(lightgbm_train_subjects)
    lightgbm_curves = {}
    for invoice, _, _ in lightgbm_test_subjects:
        hazards = predict_hazards(
            hazard_model.booster_, records, invoice, hazard_baseline,
            records_by_client=by_client,
        )
        lightgbm_curves[invoice["id"]] = _survival_from_hazards(hazards)
    lightgbm_censor_g = _censor_survival(lightgbm_train_subjects)
    lightgbm_horizons = [
        day for day in (7, 14, 30, 60, 90, 120, 180)
        if day <= max(duration for _, duration, _ in lightgbm_test_subjects)
    ]
    metrics = {
        "kaplan_meier": _model_metrics(test_subjects, km_curves, censor_g, horizons),
        "cox": _model_metrics(test_subjects, cox_curves, censor_g, horizons),
        "lightgbm_daily_hazard": _model_metrics(
            lightgbm_test_subjects, lightgbm_curves, lightgbm_censor_g, lightgbm_horizons,
        ),
    }
    return {
        "dataset": DATASET,
        "source": DATASET_URL,
        "license": LICENSE,
        "evaluation_type": "Independent public-cohort refits of Cox and pooled Kaplan–Meier plus the production daily LightGBM hazard training recipe on a smaller deterministic sample. All models use issue-time features and the same IPCW survival metrics as the synthetic evaluation. This is an external pipeline check, not a prospective result for the synthetic-trained deployed model.",
        "cleaning": cleaning,
        "split": {
            "training_issue_date_through": split_date.isoformat(),
            "test_issue_date_after": split_date.isoformat(),
            "observation_end": observation_end.isoformat(),
            "horizon_days": HORIZON,
            "train_invoice_count": len(train_subjects),
            "train_paid_count": int(sum(event for _, _, event in train_subjects)),
            "test_invoice_count": len(test_subjects),
            "test_paid_count": int(sum(event for _, _, event in test_subjects)),
            "test_right_censored_count": int(sum(not event for _, _, event in test_subjects)),
            "test_unit": "invoice",
            "benchmark_sample_count": sample_size,
            "benchmark_sample_method": "Deterministic SHA-256 sample stratified across the temporal train and test partitions; all unique invoices remain available as point-in-time client-history evidence.",
            "lightgbm_train_invoice_count": len(lightgbm_train_subjects),
            "lightgbm_train_paid_count": int(sum(event for _, _, event in lightgbm_train_subjects)),
            "lightgbm_test_invoice_count": len(lightgbm_test_subjects),
            "lightgbm_test_paid_count": int(sum(event for _, _, event in lightgbm_test_subjects)),
            "lightgbm_test_right_censored_count": int(sum(not event for _, _, event in lightgbm_test_subjects)),
            "lightgbm_sample_method": "Deterministic SHA-256 temporal sample bounded at 1,000 invoices to run the production daily risk-row expansion and LightGBM training recipe.",
        },
        "survival_metrics": metrics,
        "caveats": [
            "Payment timing and customer mix differ from the synthetic USD/EUR/GBP demonstration data; public data includes USD and CAD.",
            "The public benchmark is historical and limited to its source population and collection process.",
            "The non-commercial license permits research evaluation; do not use the dataset as a commercial training source without separate rights review.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Local path to the public dataset CSV; raw data is not copied into this repository.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/public_eval.json"))
    args = parser.parse_args()
    result = evaluate(args.csv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "split": result["split"], "survival_metrics": result["survival_metrics"]}, indent=2))


if __name__ == "__main__":
    main()
