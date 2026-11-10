from __future__ import annotations

from datetime import date
from statistics import median

from ml.features import _get


def theil_sen_slope(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    slopes = [(values[j] - values[i]) / (j - i) for i in range(len(values)) for j in range(i + 1, len(values))]
    return float(median(slopes)) if slopes else None


def client_drift(invoices: list[object], as_of: date, cadence_days: float | None = None) -> dict:
    settled = []
    for invoice in invoices:
        status = _get(invoice, "status", "")
        settled_at = _get(invoice, "settled_at") or _get(invoice, "paid_at")
        if settled_at and isinstance(settled_at, str):
            settled_at = date.fromisoformat(settled_at[:10])
        if settled_at and settled_at <= as_of and status == "PAID":
            due = _get(invoice, "due_date")
            if isinstance(due, str):
                due = date.fromisoformat(due)
            settled.append((settled_at, (settled_at - due).days))
    settled.sort(key=lambda item: item[0])
    delays = [delay for _, delay in settled]
    if len(delays) < 3:
        return {"signal": "insufficient_history", "available": False, "settled_count": len(delays), "message": "Insufficient history: fewer than 3 fully settled invoices."}
    tail = delays[-5:]
    slope = theil_sen_slope([float(v) for v in tail])
    recent_avg = median(tail[-3:])
    usual = median(delays[:-3] or delays)
    recency_days = (as_of - settled[-1][0]).days
    cadence_supported = cadence_days is not None and len(settled) >= 5
    stale = cadence_supported and recency_days > max(cadence_days * 1.5, cadence_days + 14)
    signal = "increasing_delay" if slope is not None and slope > 2 else ("recent_late" if recent_avg > usual + 2 else ("invoice_cadence_gap" if stale else "stable"))
    return {
        "available": True, "signal": signal, "settled_count": len(delays), "theil_sen_days_per_invoice": round(slope or 0, 2),
        "recent_median_delay_days": float(recent_avg), "usual_delay_days": float(usual),
        "recency_days": recency_days, "cadence_days": cadence_days if cadence_supported else None,
        "message": f"Last {len(tail)} settled invoices show a Theil–Sen delay trend of {slope or 0:.1f} days per invoice; recent median delay is {recent_avg:g} days versus a usual {usual:g} days.",
    }
