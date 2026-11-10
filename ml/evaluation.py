"""Validation-only interval calibration and client-cluster uncertainty."""
import math
from collections import defaultdict
import numpy as np
from ml.survival_metrics import uno_concordance


def quantile_day(curve, survival):
    return next((day for day, value in enumerate(curve) if day > 0 and value <= survival), None)


def fit_conformal(subjects, curves, target=.8):
    scores = []
    for row, duration, event in subjects:
        if not event:
            continue
        curve = curves[row["id"]]
        lo, hi = quantile_day(curve, .9), quantile_day(curve, .1)
        if lo is not None and hi is not None:
            scores.append(max(lo-duration, duration-hi))
    if not scores:
        return {"method": "unavailable", "correction_days": 0, "count": 0, "target": target}
    rank = min(len(scores), math.ceil((len(scores)+1)*target))
    return {"method": "split conformal quantile regression on validation paid invoices", "correction_days": sorted(scores)[rank-1], "count": len(scores), "target": target,
            "caveat": "Paid-only calibration; censoring and client dependence limit marginal coverage guarantees."}


def calibrated_band(curve, correction):
    lo, median, hi = (quantile_day(curve, p) for p in (.9, .5, .1))
    if lo is not None:
        lo = max(1, min(median or 180, lo-correction))
    if hi is not None:
        hi = max(median or 1, hi+correction)
        hi = hi if hi <= 180 else None
    return lo, median, hi


def grouped_errors(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return [{"group": group, "count":len(values),
             "median_absolute_error_days": float(np.median([r["absolute_error_days"] for r in values if r["absolute_error_days"] is not None])) if any(r["absolute_error_days"] is not None for r in values) else None,
             "coverage": sum(r["within_p10_p90"] for r in values)/len(values)} for group, values in sorted(groups.items())]


def client_bootstrap(subjects, curves_by_model, censor_g, seed=20261110, repeats=100):
    grouped = defaultdict(list)
    for subject in subjects:
        grouped[subject[0]["client_id"]].append(subject)
    keys = sorted(grouped)
    rng = np.random.default_rng(seed)
    samples = {name: [] for name in curves_by_model}
    risks = {name: {key: -sum(curve) for key, curve in curves.items()} for name, curves in curves_by_model.items()}
    for _ in range(repeats):
        cohort = [subject for key in rng.choice(keys, size=len(keys), replace=True) for subject in grouped[key]]
        for name in samples:
            score = uno_concordance(cohort, risks[name], censor_g)
            if score is not None:
                samples[name].append(score)
    return {name: {"lower":float(np.quantile(values,.025)), "upper":float(np.quantile(values,.975)), "replicates":len(values), "unit":"client"} for name, values in samples.items() if values}
