from __future__ import annotations

from datetime import date, timedelta
from statistics import median

from ml.features import _get
from ml.activity import fit_bgnbd


MIN_SETTLED_FOR_TREND = 4


def theil_sen_slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    slopes = [(values[j] - values[i]) / (j - i) for i in range(len(values)) for j in range(i + 1, len(values))]
    return float(median(slopes)) if slopes else None


def client_drift(
    invoices: list[object],
    as_of: date,
    cadence_days: float | None = None,
    activity_probability: float | None = None,
    money_at_risk_by_currency: dict[str, float] | None = None,
) -> dict:
    """Return one named signal after requiring enough paid history for delay trends."""
    settled = []
    for invoice in invoices:
        status = _get(invoice, "status", "")
        settled_at = _get(invoice, "settled_at") or _get(invoice, "paid_at")
        if settled_at and isinstance(settled_at, str):
            settled_at = date.fromisoformat(settled_at[:10])
        if settled_at and settled_at <= as_of and status == "PAID":
            due = _get(invoice, "due_date")
            if isinstance(due, str):
                due = date.fromisoformat(due[:10])
            if due:
                issue = _get(invoice, "issue_date")
                if isinstance(issue, str):
                    issue = date.fromisoformat(issue[:10])
                settled.append((issue or settled_at, settled_at, (settled_at - due).days))
    settled.sort(key=lambda item: item[0])

    issued = []
    for invoice in invoices:
        if _get(invoice, "status", "") in {"CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}:
            continue
        issue_date = _get(invoice, "issue_date")
        if isinstance(issue_date, str):
            issue_date = date.fromisoformat(issue_date[:10])
        if issue_date and issue_date <= as_of:
            issued.append(issue_date)
    issued.sort()
    if cadence_days is None and len(issued) >= 3:
        cadence_days = float(median([(issued[index] - issued[index - 1]).days for index in range(1, len(issued))]))

    delays = [delay for _, _, delay in settled]
    recency_days = (as_of - max(item[1] for item in settled)).days if settled else None
    latest_issue_gap = (as_of - issued[-1]).days if issued else None
    cadence_supported = cadence_days is not None and cadence_days > 0 and len(issued) >= 3
    has_open_balance = any(
        _get(invoice, "status", "") not in {"PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}
        and float(_get(invoice, "outstanding", 0) or 0) > 0
        for invoice in invoices
    )
    gone_quiet = bool(cadence_supported and latest_issue_gap is not None and latest_issue_gap > 2 * cadence_days and not has_open_balance)

    base = {
        "available": len(delays) >= MIN_SETTLED_FOR_TREND,
        "signal": "stable",
        "signal_type": "stable",
        "settled_count": len(delays),
        "theil_sen_days_per_invoice": None,
        "recent_median_delay_days": None,
        "usual_delay_days": None,
        "recency_days": recency_days,
        "days_since_last_invoice": latest_issue_gap,
        "cadence_days": round(cadence_days, 1) if cadence_supported else None,
        "activity_probability": round(activity_probability, 4) if activity_probability is not None else None,
        "money_at_risk_by_currency": money_at_risk_by_currency or {},
        "signal_strength": 0.0,
        "severity_score": 0.0,
    }

    if gone_quiet:
        gap_ratio = latest_issue_gap / cadence_days
        cadence_strength = min(1.0, max(0.55, (gap_ratio - 1.0) / 2.0))
        activity_factor = 1.0 - min(1.0, max(0.0, activity_probability or 0.0)) * 0.5
        strength = cadence_strength * activity_factor
        base.update({
            "signal": "gone_quiet", "signal_type": "gone_quiet", "signal_strength": round(strength, 3),
            "message": f"No invoice for {latest_issue_gap} days; the client's usual gap is about {cadence_days:.0f} days.",
        })
    elif len(delays) < MIN_SETTLED_FOR_TREND:
        base.update({
            "signal": "insufficient_history", "signal_type": "insufficient_history",
            "message": f"Not enough history: {len(delays)} settled invoices; at least {MIN_SETTLED_FOR_TREND} are needed for a delay trend.",
        })
    else:
        tail = delays[-5:]
        recent = float(median(tail[-3:]))
        usual = float(median(delays[:-3] or delays))
        slope = theil_sen_slope([float(value) for value in tail]) or 0.0
        above_usual = recent >= usual + 7
        signal_type = "paying_later" if slope > 2 and above_usual else ("later_than_usual" if above_usual else "stable")
        strength = 0.0
        if signal_type == "paying_later":
            strength = min(1.0, max(0.25, slope / 6.0, (recent - usual) / 21.0))
        elif signal_type == "later_than_usual":
            strength = min(1.0, max(0.2, (recent - usual) / 21.0))
        base.update({
            "signal": signal_type, "signal_type": signal_type,
            "theil_sen_days_per_invoice": round(slope, 2),
            "recent_median_delay_days": recent, "usual_delay_days": usual,
            "signal_strength": round(strength, 3),
            "message": f"Recent median payment delay is {recent:g} days versus a usual {usual:g} days; the trend is {slope:.1f} days per invoice.",
        })

    risk_amount = max((amount for amount in (money_at_risk_by_currency or {}).values()), default=0.0)
    base["severity_score"] = round(risk_amount * base["signal_strength"], 2)
    return base


def backtest_drift(records: list[dict], as_of: date, horizon_days: int = 90) -> dict:
    """Compare one historical signal snapshot with a recency-only top-k baseline."""
    evaluation_date = as_of - timedelta(days=2 * horizon_days)
    outcome_window_end = evaluation_date + timedelta(days=horizon_days)
    grouped: dict[str, list[dict]] = {}
    for row in records:
        grouped.setdefault(row["client_id"], []).append(row)
    historical_issues = {
        client_id: [row["issue_date"] for row in rows if row["issue_date"] <= evaluation_date and not row.get("cancelled")]
        for client_id, rows in grouped.items()
    }
    activity = fit_bgnbd(historical_issues, evaluation_date).get("probabilities", {})
    flagged: list[tuple[str, float, str]] = []
    recency: list[tuple[str, float]] = []
    actual_by_client: dict[str, bool] = {}
    for client_id, rows in grouped.items():
        history = []
        for row in rows:
            if row["issue_date"] > evaluation_date:
                continue
            snapshot = dict(row)
            if row["cancelled_at"] and row["cancelled_at"] <= evaluation_date:
                snapshot.update(status="CANCELLED", outstanding=0, settled_at=None)
            elif row["outcome_paid_at"] and row["outcome_paid_at"] <= evaluation_date:
                snapshot.update(status="PAID", outstanding=0, settled_at=row["outcome_paid_at"])
            else:
                partial_paid = float(row.get("partial_amount", 0)) if row.get("partial_at") and row["partial_at"] <= evaluation_date else 0.0
                snapshot.update(
                    status="PARTIALLY_PAID" if partial_paid else ("OVERDUE" if row["due_date"] < evaluation_date else "SENT"),
                    outstanding=max(0.0, float(row["total"]) - partial_paid), settled_at=None,
                )
            history.append(snapshot)
        issued = sorted({row["issue_date"] for row in history})
        gaps = sorted((issued[index] - issued[index - 1]).days for index in range(1, len(issued)))
        cadence = float(median(gaps)) if gaps else None
        open_by_currency: dict[str, float] = {}
        paid_amounts: dict[str, list[float]] = {}
        for row in history:
            currency = row["currency"]
            if row["cancelled"] and row.get("cancelled_at") and row["cancelled_at"] <= evaluation_date:
                continue
            if row.get("outcome_paid_at") and row["outcome_paid_at"] <= evaluation_date:
                paid_amounts.setdefault(currency, []).append(float(row["total"]))
            else:
                balance = float(row["total"])
                if row.get("partial_at") and row["partial_at"] <= evaluation_date:
                    balance -= float(row.get("partial_amount", 0))
                open_by_currency[currency] = open_by_currency.get(currency, 0.0) + max(0.0, balance)
        money_at_risk = {
            currency: open_by_currency.get(currency, 0.0) + (median(sorted(paid_amounts.get(currency, []))) if paid_amounts.get(currency) else 0.0)
            for currency in (set(paid_amounts) | set(open_by_currency))
        }
        drift = client_drift(history, evaluation_date, cadence, activity.get(client_id), money_at_risk)
        if cadence and issued:
            recency.append((client_id, (evaluation_date - issued[-1]).days / cadence))
        if drift["signal_type"] in {"paying_later", "later_than_usual", "gone_quiet"}:
            flagged.append((client_id, float(drift["severity_score"]), drift["signal_type"]))

        prior_delays = [
            (row["settled_at"] - row["due_date"]).days
            for row in history if row["status"] == "PAID" and row.get("settled_at") and row["settled_at"] <= evaluation_date
        ]
        future = [
            row for row in rows if evaluation_date < row["issue_date"] <= outcome_window_end
            and row["status"] == "PAID" and row.get("settled_at") and row["settled_at"] <= as_of
        ]
        future_delays = [(row["settled_at"] - row["due_date"]).days for row in future]
        went_quiet = not any(evaluation_date < row["issue_date"] <= outcome_window_end for row in rows)
        got_slower = bool(prior_delays and future_delays and median(future_delays) >= median(prior_delays) + 7)
        actual_by_client[client_id] = went_quiet or got_slower

    flagged.sort(key=lambda row: (-row[1], row[0]))
    model_ids = [client_id for client_id, _, _ in flagged]
    model_hits = sum(actual_by_client.get(client_id, False) for client_id in model_ids)
    recency.sort(key=lambda row: (-row[1], row[0]))
    baseline_ids = [client_id for client_id, _ in recency[:len(model_ids)]]
    baseline_hits = sum(actual_by_client.get(client_id, False) for client_id in baseline_ids)
    return {
        "evaluation_as_of": evaluation_date.isoformat(), "outcome_window_end": outcome_window_end.isoformat(),
        "outcome_horizon_days": horizon_days,
        "flagged_client_count": len(model_ids), "true_positive_count": model_hits,
        "precision": round(model_hits / len(model_ids), 4) if model_ids else None,
        "recency_only_flagged_client_count": len(baseline_ids), "recency_only_true_positive_count": baseline_hits,
        "recency_only_precision": round(baseline_hits / len(baseline_ids), 4) if baseline_ids else None,
        "flagged_clients": [{"client_id": client_id, "signal_type": signal,
                              "actual_went_quiet_or_got_slower": actual_by_client.get(client_id, False)}
                             for client_id, _, signal in flagged],
        "recency_only_client_ids": baseline_ids,
        "outcome_definition": "No invoice issue in the next 90 days, or future paid-invoice median delay at least 7 days above the preceding median.",
    }
