from __future__ import annotations

from datetime import date

import numpy as np
from scipy.optimize import minimize
from scipy.special import betaln, expit, gammaln


def fit_bgnbd(issue_dates: dict[str, list[date]], as_of: date) -> dict:
    """Fit BG/NBD purchase and dropout parameters from invoice cadence data."""
    observations = []
    for client_id, values in issue_dates.items():
        dates = sorted(day for day in values if day <= as_of)
        if not dates:
            continue
        frequency = max(0, len(dates) - 1)
        recency = max(0.0, (dates[-1] - dates[0]).days / 30.0)
        tenure = max(1e-3, (as_of - dates[0]).days / 30.0)
        observations.append((client_id, frequency, recency, tenure))
    if len(observations) < 5:
        return {"available": False, "model": "BG/NBD", "probabilities": {}, "reason": "At least five clients are needed to fit activity parameters."}

    def objective(log_parameters):
        r, alpha, a, b = np.exp(np.clip(log_parameters, -8, 8))
        total_log_likelihood = 0.0
        for _, frequency, recency, tenure in observations:
            x = frequency
            log_common = gammaln(r + x) - gammaln(r) + r * np.log(alpha)
            alive = betaln(a, b + x) - betaln(a, b) + log_common - (r + x) * np.log(alpha + tenure)
            if x > 0:
                dead = (betaln(a + 1, b + x - 1) - betaln(a, b) + log_common
                        - (r + x) * np.log(alpha + recency))
                total_log_likelihood += float(np.logaddexp(alive, dead))
            else:
                total_log_likelihood += float(alive)
        # Weak log-normal regularization stabilizes a small, synthetic client cohort.
        return -total_log_likelihood + 0.02 * float(log_parameters @ log_parameters)

    initial = np.log(np.asarray([1.0, 12.0, 1.0, 3.0]))
    result = minimize(objective, initial, method="L-BFGS-B", bounds=[(-7, 7)] * 4, options={"maxiter": 500, "ftol": 1e-11})
    r, alpha, a, b = np.exp(np.clip(result.x, -8, 8))
    probabilities = {}
    for client_id, frequency, recency, tenure in observations:
        if frequency == 0:
            alive_probability = 1.0
        else:
            log_odds = (np.log(a) - np.log(b + frequency - 1)
                        + (r + frequency) * (np.log(alpha + tenure) - np.log(alpha + recency)))
            alive_probability = float(expit(-log_odds))
        probabilities[client_id] = round(alive_probability, 4)
    return {
        "available": bool(result.success), "model": "BG/NBD", "probabilities": probabilities,
        "parameters": {"r": float(r), "alpha": float(alpha), "a": float(a), "b": float(b)},
        "client_count": len(observations), "as_of_date": as_of.isoformat(), "iterations": int(result.nit),
    }
