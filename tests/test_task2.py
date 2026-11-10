from datetime import date, timedelta
from types import SimpleNamespace
import pytest
from app.services.forecast import conditional_tail, _quantile, collection_probability
from app.services.policy import decide
from ml.drift import client_drift


def test_unpaid_tail_conditions_at_current_age_without_tomorrow_floor():
    today = date(2026, 11, 11)
    target = {"id": "open", "issue_date": today - timedelta(days=130), "due_date": today - timedelta(days=100)}
    history = [{"id": str(i), "issue_date": today - timedelta(days=300 + i), "settled_at": today - timedelta(days=260 + i)} for i in range(10)]
    curve = conditional_tail(target, history, today)
    assert curve[0] == pytest.approx(1)
    assert all(a >= b for a, b in zip(curve, curve[1:]))
    assert _quantile(curve, .5, today)["days"] > 1
    assert _quantile([1] * 181, .1, today) is None
    assert collection_probability([], today) == .35


def test_reminder_history_advances_ladder_and_enforces_cooldown():
    today = date(2026, 11, 11)
    invoice = SimpleNamespace(id="i", outstanding=100, currency="USD", disputed=False, status="OVERDUE",
        due_date=today - timedelta(days=30), reminder_step=0, partial_payment_allowed=False,
        reminder_events=[{"sent_at": (today-timedelta(days=20)).isoformat(), "tone":"friendly"},
                         {"sent_at": (today-timedelta(days=10)).isoformat(), "tone":"firm"}])
    action = decide(invoice, 0, today=today, probability_14d=.1)
    assert action["action"] != "friendly"
    invoice.reminder_events[-1]["sent_at"] = today.isoformat()
    assert decide(invoice, 0, today=today)["action"] == "wait"


def test_chronic_lateness_is_distinct_from_stable_trend():
    today = date(2026, 11, 11)
    rows = [{"status":"PAID", "issue_date":today-timedelta(days=200+i*10),
             "due_date":today-timedelta(days=180+i*10), "settled_at":today-timedelta(days=110+i*10)} for i in range(10)]
    rows.append({"status": "SENT", "issue_date": today, "due_date": today, "outstanding": 100})
    assert client_drift(rows, today)["signal_type"] == "habitually_late"


def test_risk_row_balance_does_not_leak_final_settlement():
    from ml.train import _row_features
    from ml.features import FEATURES
    from decimal import Decimal
    today = date(2026, 11, 11)
    invoice = {"id": "historical", "client_id": "c", "issue_date": today-timedelta(days=30),
               "due_date":today, "total":Decimal(100), "outstanding":Decimal(0), "settled_at":today,
               "partial_at":None, "partial_amount":0}
    values = _row_features([invoice], invoice, today-timedelta(days=5))
    assert values[FEATURES.index("log_outstanding")] == pytest.approx(__import__('math').log1p(100))
