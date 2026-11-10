"""Counterfactual reminder replay; assumptions, never observed causal effects."""

from datetime import timedelta
from types import SimpleNamespace


def replay(subjects, seed=20261110):
    del seed  # Kept for compatibility with existing training callers.
    from app.services.policy import decide

    total_exposure = 0.0
    baseline_weighted_days = 0.0
    eligible_cases: list[tuple[float, int]] = []
    paid_count = 0
    for row, duration, event in subjects:
        if not event:
            continue
        paid_count += 1
        amount = float(row["total"])
        total_exposure += amount
        baseline_weighted_days += amount * duration
        review_date = row["due_date"] + timedelta(days=3)
        paid_at = row.get("outcome_paid_at")
        if not paid_at or review_date >= paid_at:
            continue
        invoice = SimpleNamespace(
            outstanding=amount, currency=row["currency"], disputed=False,
            status="OVERDUE", due_date=row["due_date"], reminder_step=0,
            reminder_events=[], partial_payment_allowed=row.get("partial_payment_allowed", False),
        )
        decision = decide(invoice, .1, today=review_date, probability_14d=.4)
        if decision["action"] not in {"friendly", "firm"}:
            continue
        days_advanced = min(5, max(0, (paid_at - review_date).days))
        if days_advanced:
            eligible_cases.append((amount, days_advanced))

    base_dso = baseline_weighted_days / total_exposure if total_exposure else None
    sensitivity = []
    for probability in (.05, .10, .20):
        weighted_days_saved = sum(amount * probability * days for amount, days in eligible_cases)
        saved_dso = weighted_days_saved / total_exposure if total_exposure else None
        sensitivity.append({
            "assumed_effect_probability": probability,
            "expected_dso_saved_days": round(saved_dso, 3) if saved_dso is not None else None,
        })

    default = sensitivity[1]
    saved = default["expected_dso_saved_days"]
    return {
        "label": "Simulation · assumed effects, not observed uplift",
        "invoice_count": paid_count,
        "eligible_invoice_count": len(eligible_cases),
        "simulated_accelerated_count": round(len(eligible_cases) * .10, 2),
        "no_reminder_weighted_dso_days": round(base_dso, 3) if base_dso is not None else None,
        "policy_weighted_dso_days": round(base_dso - saved, 3) if base_dso is not None and saved is not None else None,
        "simulated_dso_saved_days": round(saved, 3) if saved is not None else None,
        "sensitivity": sensitivity,
        "assumption": "At due+3, an eligible friendly or firm reminder has an assumed 5%, 10%, or 20% chance of advancing settlement by up to five days. The table is a deterministic sensitivity analysis, not a causal estimate from observed reminder outcomes.",
    }
