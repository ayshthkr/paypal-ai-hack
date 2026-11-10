from __future__ import annotations

import numpy as np
from scipy.optimize import minimize


def uno_concordance(subjects, risks: dict[str, float], censor_survival: list[float]) -> float | None:
    """Uno's IPCW concordance for right-censored invoice settlement times."""
    comparable_weight = 0.0
    concordant_weight = 0.0
    for invoice, duration, event in subjects:
        if not event or invoice["id"] not in risks:
            continue
        g_index = min(max(0, duration - 1), len(censor_survival) - 1)
        g = max(1e-6, censor_survival[g_index])
        weight = 1.0 / (g * g)
        risk = risks[invoice["id"]]
        for other, other_duration, _ in subjects:
            if other_duration <= duration or other["id"] not in risks:
                continue
            comparable_weight += weight
            other_risk = risks[other["id"]]
            concordant_weight += weight * (1.0 if risk > other_risk else 0.5 if risk == other_risk else 0.0)
    return concordant_weight / comparable_weight if comparable_weight else None


def binary_auc(labels: list[int], scores: list[float]) -> float | None:
    cases = [score for label, score in zip(labels, scores, strict=True) if label == 1]
    controls = [score for label, score in zip(labels, scores, strict=True) if label == 0]
    if not cases or not controls:
        return None
    total = 0.0
    for case in cases:
        for control in controls:
            total += 1.0 if case > control else 0.5 if case == control else 0.0
    return total / (len(cases) * len(controls))


def time_dependent_auc(subjects, predictions: dict[str, list[float]], offset_days: int) -> float | None:
    labels: list[int] = []
    scores: list[float] = []
    for invoice, duration, event in subjects:
        survival = predictions.get(invoice["id"])
        if not survival:
            continue
        terms = max(0, (invoice["due_date"] - invoice["issue_date"]).days)
        horizon = min(len(survival) - 1, max(1, terms + offset_days))
        if event and duration <= horizon:
            label = 1
        elif duration >= horizon:
            label = 0
        else:
            # Censored before the selected due-date horizon, so its label is unknown.
            continue
        labels.append(label)
        scores.append(1.0 - survival[horizon])
    return binary_auc(labels, scores)


def fit_cox(X: np.ndarray, durations: np.ndarray, events: np.ndarray, horizon: int = 180) -> dict:
    """Fit a ridge-stabilized Cox PH model and Breslow baseline cumulative hazard."""
    X = np.asarray(X, dtype=float)
    durations = np.asarray(durations, dtype=int)
    events = np.asarray(events, dtype=int)
    mean = X.mean(axis=0)
    scale = X.std(axis=0)
    scale[scale < 1e-8] = 1.0
    Z = (X - mean) / scale
    event_days = np.unique(durations[events == 1])

    def objective(beta):
        eta = np.clip(Z @ beta, -25, 25)
        likelihood = 0.0
        gradient = np.zeros_like(beta)
        for day in event_days:
            event_mask = (durations == day) & (events == 1)
            risk_mask = durations >= day
            count = int(event_mask.sum())
            risk_weights = np.exp(eta[risk_mask])
            denominator = max(float(risk_weights.sum()), 1e-12)
            likelihood += float(eta[event_mask].sum() - count * np.log(denominator))
            weighted_mean = (Z[risk_mask] * risk_weights[:, None]).sum(axis=0) / denominator
            gradient += Z[event_mask].sum(axis=0) - count * weighted_mean
        ridge = 0.01
        likelihood -= ridge * float(beta @ beta)
        gradient -= 2.0 * ridge * beta
        return -likelihood, -gradient

    result = minimize(objective, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B", options={"maxiter": 300, "ftol": 1e-10})
    beta = result.x if np.all(np.isfinite(result.x)) else np.zeros(X.shape[1])
    eta = np.clip(Z @ beta, -25, 25)
    baseline = np.zeros(horizon + 1, dtype=float)
    cumulative = 0.0
    for day in range(1, horizon + 1):
        risk_mask = durations >= day
        event_count = int(((durations == day) & (events == 1)).sum())
        if event_count and risk_mask.any():
            cumulative += event_count / max(float(np.exp(eta[risk_mask]).sum()), 1e-12)
        baseline[day] = cumulative
    return {"mean": mean, "scale": scale, "beta": beta, "baseline_cumulative_hazard": baseline,
            "converged": bool(result.success), "iterations": int(result.nit)}


def predict_cox_survival(X: np.ndarray, model: dict) -> list[list[float]]:
    Z = (np.asarray(X, dtype=float) - model["mean"]) / model["scale"]
    risks = np.exp(np.clip(Z @ model["beta"], -25, 25))
    baseline = model["baseline_cumulative_hazard"]
    return [np.exp(-baseline * risk).astype(float).tolist() for risk in risks]
