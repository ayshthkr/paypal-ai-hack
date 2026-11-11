"""Validation-only interval calibration and client-cluster uncertainty."""
import math
from collections import defaultdict
import numpy as np
from scipy.stats import ks_2samp
from ml.survival_metrics import uno_concordance


def quantile_day(curve, survival):
    return next((day for day, value in enumerate(curve) if day > 0 and value <= survival), None)


def fit_conformal(subjects, curves, target=.8):
    scores = []
    by_currency = defaultdict(list)
    for row, duration, event in subjects:
        if not event:
            continue
        curve = curves[row["id"]]
        lo, hi = quantile_day(curve, .9), quantile_day(curve, .1)
        if lo is not None and hi is not None:
            score = max(lo-duration, duration-hi)
            scores.append(score)
            by_currency[row["currency"]].append(score)
    if not scores:
        return {"method": "unavailable", "correction_days": 0, "count": 0, "target": target, "currency_corrections": {}}

    def correction(values):
        rank = min(len(values), math.ceil((len(values) + 1) * target))
        return max(0, int(math.ceil(sorted(values)[rank - 1])))

    currency_corrections = {
        currency: {"correction_days": correction(values), "count": len(values)}
        for currency, values in sorted(by_currency.items()) if len(values) >= 20
    }
    return {
        "method": "split conformal interval correction fitted on validation paid invoices, with per-currency calibration when n ≥ 20",
        "correction_days": correction(scores), "count": len(scores), "target": target,
        "currency_corrections": currency_corrections,
        "caveat": "Paid-only calibration; censoring, small currency slices, and client dependence limit marginal coverage guarantees.",
    }


def correction_for_currency(calibration, currency):
    return calibration.get("currency_corrections", {}).get(currency, {}).get(
        "correction_days", calibration.get("correction_days", 0),
    )


def calibrated_band(curve, correction):
    lo, median, hi = (quantile_day(curve, p) for p in (.9, .5, .1))
    if lo is not None:
        lo = max(1, min(median or 180, lo-correction))
    if hi is not None:
        hi = max(median or 1, hi+correction)
        hi = hi if hi <= 180 else None
    return lo, median, hi


def grouped_errors(rows, key, coverage_key="within_p10_p90"):
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    result = []
    for group, values in sorted(groups.items()):
        calibrated = coverage_key == "within_interval" or any(
            "interval_lower_day" in row or "coverage_interval_lower_date" in row for row in values
        )
        date_bounds = ("coverage_interval_lower_date", "coverage_interval_upper_date") if calibrated else ("p10_date", "p90_date")
        def interval_bounds(row):
            if row.get("interval_lower_day") is not None and row.get("interval_upper_day") is not None:
                return float(row["interval_lower_day"]), float(row["interval_upper_day"])
            lower, upper = (row.get(date_bounds[0]), row.get(date_bounds[1]))
            if lower and upper:
                return float((np.datetime64(upper) - np.datetime64(lower)) / np.timedelta64(1, "D")), 0.0
            return None
        eligible = [row for row in values if interval_bounds(row) is not None]
        widths = []
        for row in eligible:
            if row.get("interval_lower_day") is not None:
                widths.append(float(row["interval_upper_day"] - row["interval_lower_day"]))
            else:
                lower, upper = (row[date_bounds[0]], row[date_bounds[1]])
                widths.append(float((np.datetime64(upper) - np.datetime64(lower)) / np.timedelta64(1, "D")))
        errors = [row["absolute_error_days"] for row in values if row["absolute_error_days"] is not None]
        covered = sum(bool(row.get(coverage_key)) for row in eligible)
        result.append({
            "group": group,
            "count": len(values),
            "eligible_interval_count": len(eligible),
            "excluded_unbounded_count": len(values) - len(eligible),
            "covered_count": covered,
            "median_absolute_error_days": float(np.median(errors)) if errors else None,
            "coverage": covered / len(eligible) if eligible else None,
            "median_interval_width_days": float(np.median(widths)) if widths else None,
            "coverage_definition": "validation-calibrated interval" if calibrated else "raw model P10–P90 interval",
        })
    return result


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


def horizon_calibration(subjects, predictions, censor_survival, horizon: int, bins: int = 10) -> dict:
    """IPCW calibration for P(payment by horizon), retaining known censored outcomes."""
    observations = []
    excluded = 0
    censored_known_negative = 0
    for invoice, duration, event in subjects:
        curve = predictions.get(invoice["id"])
        if not curve or len(curve) <= horizon:
            continue
        if event and duration <= horizon:
            label = 1.0
            index = max(0, duration - 1)
        elif duration >= horizon:
            label = 0.0
            index = horizon
            censored_known_negative += int(event == 0)
        else:
            excluded += 1
            continue
        g = max(1e-6, censor_survival[min(index, len(censor_survival) - 1)])
        observations.append((1.0 - float(curve[horizon]), label, 1.0 / g))

    ordered = sorted(observations, key=lambda item: item[0])
    groups = np.array_split(np.asarray(ordered, dtype=float), min(bins, len(ordered))) if ordered else []
    deciles = []
    for index, group in enumerate(groups, start=1):
        if not len(group):
            continue
        weights = group[:, 2]
        deciles.append({
            "bin": index,
            "count": int(len(group)),
            "mean_predicted": float(np.average(group[:, 0], weights=weights)),
            "ipcw_observed": float(np.average(group[:, 1], weights=weights)),
            "effective_weight": float(weights.sum()),
        })
    return {
        "horizon_days": horizon,
        "count": len(observations),
        "censored_known_negative_count": censored_known_negative,
        "censored_before_horizon_excluded": excluded,
        "method": "inverse probability of censoring weighted; events by horizon and invoices observed through horizon",
        "deciles": deciles,
    }


def residual_drift(calibration_subjects, calibration_curves, test_subjects, test_curves) -> dict:
    """Compare paid-invoice signed P50 residuals across calibration and test periods."""
    def residuals(subjects, curves):
        values = []
        for invoice, duration, event in subjects:
            if not event:
                continue
            curve = curves.get(invoice["id"])
            if not curve:
                continue
            median_day = quantile_day(curve, 0.5)
            if median_day is not None:
                values.append(float(duration - median_day))
        return values

    calibration = residuals(calibration_subjects, calibration_curves)
    test = residuals(test_subjects, test_curves)
    if len(calibration) < 2 or len(test) < 2:
        return {
            "method": "two-sample Kolmogorov–Smirnov test on signed paid-invoice P50 residuals",
            "calibration_count": len(calibration), "test_count": len(test),
            "statistic": None, "p_value": None, "drift_detected_at_0_05": None,
            "caveat": "Insufficient paid invoices for a distribution comparison.",
        }
    result = ks_2samp(calibration, test, alternative="two-sided", method="auto")
    return {
        "method": "two-sample Kolmogorov–Smirnov test on signed paid-invoice P50 residuals",
        "calibration_count": len(calibration), "test_count": len(test),
        "calibration_median_residual_days": float(np.median(calibration)),
        "test_median_residual_days": float(np.median(test)),
        "statistic": float(result.statistic), "p_value": float(result.pvalue),
        "drift_detected_at_0_05": bool(result.pvalue < 0.05),
        "caveat": "Paid-only residual diagnostic; it is a drift check, not a censoring-adjusted calibration guarantee.",
    }


def coverage_by_issue_month(rows) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        if row.get("issue_date"):
            grouped[str(row["issue_date"])[:7]].append(row)
    monthly = []
    for month, values in sorted(grouped.items()):
        eligible = [
            row for row in values
            if row.get("coverage_interval_lower_date")
            and row.get("coverage_interval_upper_date")
        ]
        monthly.append({
            "month": month,
            "paid_invoice_count": len(values),
            "eligible_interval_count": len(eligible),
            "excluded_count": len(values) - len(eligible),
            "covered_count": sum(bool(row.get("within_interval")) for row in eligible),
            "coverage": (
                sum(bool(row.get("within_interval")) for row in eligible) / len(eligible)
                if eligible else None
            ),
        })
    return monthly
