"""Canonical, evidence-at-as-of feature builder used by training and inference."""

from __future__ import annotations

import math
import statistics
from datetime import date

FEATURE_SCHEMA_VERSION = "latepayer-features-v1"
FEATURES = [
    "log_outstanding", "terms_days", "age_days", "days_until_due", "overdue_days",
    "issue_month_sin", "issue_month_cos", "as_of_month_sin", "as_of_month_cos",
    "weekday_sin", "weekday_cos", "history_count", "history_late_rate", "recent_delay_days",
    "delay_std_days", "history_missing", "other_open_exposure_log", "currency_is_usd",
    "currency_is_eur", "currency_is_gbp",
]


def _get(record: object, key: str, default=None):
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _amount(value) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def features(invoice: object, as_of: date, history: list[object], all_invoices: list[object] | None = None) -> dict[str, float]:
    issue = _get(invoice, "issue_date", as_of)
    due = _get(invoice, "due_date", issue)
    if isinstance(issue, str):
        issue = date.fromisoformat(issue)
    if isinstance(due, str):
        due = date.fromisoformat(due)
    age = max(0, (as_of - issue).days)
    known_delays: list[int] = []
    for item in history:
        settled_at = _get(item, "settled_at")
        if settled_at is None:
            settled_at = _get(item, "paid_at")
        item_issue = _get(item, "issue_date")
        if isinstance(item_issue, str):
            item_issue = date.fromisoformat(item_issue)
        if isinstance(settled_at, str):
            settled_at = date.fromisoformat(settled_at[:10])
        # Include only invoices issued earlier and fully settled by this exact information cutoff.
        if item_issue and item_issue < issue and settled_at and settled_at <= as_of:
            item_due = _get(item, "due_date", item_issue)
            if isinstance(item_due, str):
                item_due = date.fromisoformat(item_due)
            known_delays.append((settled_at - item_due).days)
    recent = known_delays[-5:]
    late_rate = sum(delay > 0 for delay in known_delays) / len(known_delays) if known_delays else 0.0
    recent_delay = float(statistics.median(recent)) if recent else 0.0
    delay_std = float(statistics.pstdev(recent)) if len(recent) > 1 else 0.0
    exposure = 0.0
    if all_invoices:
        client_id = _get(invoice, "client_id")
        for item in all_invoices:
            if item is invoice or _get(item, "id") == _get(invoice, "id"):
                continue
            item_issue = _get(item, "issue_date")
            if isinstance(item_issue, str):
                item_issue = date.fromisoformat(item_issue)
            if _get(item, "client_id") != client_id or not item_issue or item_issue > as_of:
                continue
            settled = _get(item, "settled_at") or _get(item, "paid_at")
            cancelled_at = _get(item, "cancelled_at")
            partial_at = _get(item, "partial_at")
            for key, value in (("settled", settled), ("cancelled_at", cancelled_at), ("partial_at", partial_at)):
                if isinstance(value, str):
                    if key == "settled":
                        settled = date.fromisoformat(value[:10])
                    elif key == "cancelled_at":
                        cancelled_at = date.fromisoformat(value[:10])
                    else:
                        partial_at = date.fromisoformat(value[:10])
            if (settled and settled <= as_of) or (cancelled_at and cancelled_at <= as_of):
                continue
            amount = _get(item, "total", _get(item, "outstanding", 0))
            amount = _amount(amount)
            has_timeline = "outcome_paid_at" in item if isinstance(item, dict) else hasattr(item, "outcome_paid_at")
            if has_timeline:
                # Synthetic training data has an explicit event timeline; never read its
                # final snapshot balance when constructing an earlier feature row.
                if partial_at and partial_at <= as_of:
                    amount -= _amount(_get(item, "partial_amount", _get(item, "paid", 0)))
            elif hasattr(item, "outstanding") or (isinstance(item, dict) and "outstanding" in item):
                # Runtime SQL records are current snapshots with no full event timeline.
                amount = _amount(_get(item, "outstanding", amount))
            elif partial_at and partial_at <= as_of:
                amount -= _amount(_get(item, "partial_amount", _get(item, "paid", 0)))
            exposure += max(0.0, amount)
    issue_angle = 2 * math.pi * (issue.month - 1) / 12
    asof_angle = 2 * math.pi * (as_of.month - 1) / 12
    week_angle = 2 * math.pi * as_of.weekday() / 7
    currency = str(_get(invoice, "currency", "USD")).upper()
    outstanding = _amount(_get(invoice, "outstanding", _get(invoice, "total", 0)))
    return {
        "log_outstanding": math.log1p(outstanding),
        "terms_days": float(max(0, (due - issue).days)),
        "age_days": float(age),
        "days_until_due": float((due - as_of).days),
        "overdue_days": float(max(0, (as_of - due).days)),
        "issue_month_sin": math.sin(issue_angle), "issue_month_cos": math.cos(issue_angle),
        "as_of_month_sin": math.sin(asof_angle), "as_of_month_cos": math.cos(asof_angle),
        "weekday_sin": math.sin(week_angle), "weekday_cos": math.cos(week_angle),
        "history_count": float(len(known_delays)), "history_late_rate": late_rate,
        "recent_delay_days": recent_delay, "delay_std_days": delay_std,
        "history_missing": float(len(known_delays) < 3), "other_open_exposure_log": math.log1p(exposure),
        "currency_is_usd": float(currency == "USD"), "currency_is_eur": float(currency == "EUR"),
        "currency_is_gbp": float(currency == "GBP"),
    }
