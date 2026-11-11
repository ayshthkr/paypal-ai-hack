"""Shared survival-curve scoring steps used by the API and offline replay."""

from __future__ import annotations

import math
from datetime import date

import numpy as np


def survival_from_hazards(hazards: list[float], horizon: int = 180) -> list[float]:
    curve = [1.0]
    survival = 1.0
    for hazard in hazards[:horizon]:
        survival *= 1.0 - min(1.0, max(0.0, float(hazard)))
        curve.append(float(survival))
    while len(curve) < horizon + 1:
        curve.append(curve[-1])
    return curve


def conditional_survival_from_absolute(
    absolute_survival: list[float], age: int, horizon: int = 180,
) -> list[float]:
    last = len(absolute_survival) - 1
    if last < 0:
        return [1.0] * (horizon + 1)
    start = min(max(0, age), last)
    denominator = max(1e-12, float(absolute_survival[start]))
    return [
        min(1.0, max(0.0, float(absolute_survival[min(start + offset, last)]) / denominator))
        for offset in range(horizon + 1)
    ]


def calibrate_survival_curve(curve: list[float], calibration: dict) -> list[float]:
    """Anchor the monotone curve to a validation-fitted 14-day calibration map."""
    if not calibration.get("applied") or len(curve) <= 14:
        return curve
    thresholds_x = np.asarray(calibration.get("thresholds_x", [0.0, 1.0]), dtype=float)
    thresholds_y = np.asarray(calibration.get("thresholds_y", [0.0, 1.0]), dtype=float)
    if not len(thresholds_x) or len(thresholds_x) != len(thresholds_y):
        return curve
    raw_p14 = min(1.0, max(0.0, 1.0 - curve[14]))
    target_p14 = min(1.0, max(0.0, float(np.interp(raw_p14, thresholds_x, thresholds_y))))
    if abs(target_p14 - raw_p14) < 1e-12:
        return curve

    calibrated: list[float] = []
    for day, survival in enumerate(curve):
        raw_probability = 1.0 - survival
        if day <= 14:
            probability = raw_probability * target_p14 / raw_p14 if raw_p14 > 1e-12 else target_p14 * day / 14
        elif raw_p14 < 1.0 - 1e-12:
            probability = target_p14 + (raw_probability - raw_p14) * (1.0 - target_p14) / (1.0 - raw_p14)
        else:
            probability = target_p14
        calibrated.append(1.0 - min(1.0, max(0.0, probability)))
    return calibrated


def conditional_tail(target: dict, history: list[dict], today: date, horizon: int = 180) -> list[float]:
    """Kernel survival S(age+t)/S(age), with shrinkage for thin client history."""
    age = max(0, (today - target["issue_date"]).days)
    durations = [
        max(1, (row["settled_at"] - row["issue_date"]).days)
        for row in history
        if row["id"] != target["id"] and row["issue_date"] < target["issue_date"]
    ]
    terms = max(0, (target["due_date"] - target["issue_date"]).days)
    prior = max(7, terms + 14)
    values = durations + [prior] * max(0, 5 - len(durations))

    def survival(day: int) -> float:
        sigma = 0.5
        return sum(
            0.5 * math.erfc((math.log(max(0.01, day)) - math.log(value)) / (sigma * math.sqrt(2)))
            for value in values
        ) / len(values)

    denominator = max(1e-12, survival(age))
    return [
        min(1.0, max(0.0, survival(age + offset) / denominator))
        for offset in range(horizon + 1)
    ]


def collection_probability(records: list[dict], today: date) -> float:
    """Beta-shrunk eventual-recovery estimate for invoices mature at least 180 days."""
    mature = [row for row in records if (today - row["due_date"]).days >= 180]
    late_risk = [
        row for row in mature
        if not row.get("settled_at") or (row["settled_at"] - row["due_date"]).days >= 90
    ]
    recovered = sum(bool(row.get("settled_at") and row["settled_at"] <= today) for row in late_risk)
    return round((recovered + 3.5) / (len(late_risk) + 10), 4)


def apply_promise_scenario(
    curve: list[float], promised_date, today: date, recovery_probability: float = 1.0,
    weight: float = 0.25, horizon: int = 180,
) -> tuple[list[float], dict]:
    if isinstance(promised_date, str):
        try:
            promised_date = date.fromisoformat(promised_date[:10])
        except ValueError:
            promised_date = None
    if not promised_date:
        return curve, {"applied": False, "status": "none", "weight": 0.0}
    if promised_date <= today:
        return curve, {"applied": False, "status": "expired", "date": promised_date.isoformat(), "weight": 0.0}
    promise_day = (promised_date - today).days
    if promise_day > horizon:
        return curve, {"applied": False, "status": "after_horizon", "date": promised_date.isoformat(), "weight": 0.0}
    recovery = min(1.0, max(0.0, float(recovery_probability)))
    if recovery <= 0:
        return curve, {"applied": False, "status": "no_recovery_evidence", "date": promised_date.isoformat(), "weight": 0.0}
    adjusted = []
    for day, survival in enumerate(curve):
        base_recovered_cdf = min(1.0, max(0.0, (1.0 - survival) / recovery))
        promised_cdf = 1.0 if day >= promise_day else 0.0
        scenario_cdf = (1.0 - weight) * base_recovered_cdf + weight * promised_cdf
        adjusted.append(1.0 - recovery * scenario_cdf)
    return adjusted, {
        "applied": True, "status": "scenario", "date": promised_date.isoformat(),
        "weight": weight,
        "method": "Illustrative mixture: 25% of eventual recovery is placed on the stated date; remaining mass follows the forecast curve.",
        "caveat": "Promise adherence is not estimated from training data; the adjustment is a scenario, not a guarantee.",
    }


def apply_production_overlays(
    curve: list[float], target: dict, records: list[dict], history: list[dict],
    today: date, calibration: dict, horizon: int = 180,
) -> tuple[list[float], float, dict]:
    """Apply the post-model steps shared by online scoring and the train replay."""
    overdue = max(0, (today - target["due_date"]).days)
    if overdue > 0:
        tail = conditional_tail(target, history, today, horizon)
        weight = min(1.0, overdue / 30.0)
        curve = [
            (1 - weight) * modeled + weight * empirical
            for modeled, empirical in zip(curve, tail, strict=True)
        ]
    curve = calibrate_survival_curve(curve, calibration)
    recovery = collection_probability(records, today) if overdue >= 90 else 1.0
    if overdue >= 90:
        curve = [max(1.0 - recovery, survival) for survival in curve]
    curve, promise = apply_promise_scenario(
        curve, target.get("promised_payment_date"), today, recovery, horizon=horizon,
    )
    return curve, recovery, promise
