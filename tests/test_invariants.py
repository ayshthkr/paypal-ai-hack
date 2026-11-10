from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app import main
from app.config import Settings
from app.models import Client, Invoice, InvoiceEvent, Payment, PolicyDecisionLog, Prediction, Recommendation, ReplyTriage
from app.security import COOKIE_NAME, create_session
from app.services.drift import backtest_drift, client_drift, theil_sen_slope
from app.services.paypal import PayPalAdapter, PayPalError, normalize_invoice
from app.services.policy import decide
from app.services import sync as sync_service
from app.services import ai as ai_service
from ml.features import features
from ml.synthetic import OPEN_BUCKET_COUNTS, generate_records
from ml.activity import fit_bgnbd
from ml.train import _duration, _survival_from_hazards, km_hazards


def _invoice(invoice_id: str, client_id: str, *, source="demo", status="OVERDUE", due=None):
    today = date.today()
    return Invoice(
        id=invoice_id, invoice_number=f"LP-{invoice_id}", client_id=client_id,
        issue_date=today - timedelta(days=30), due_date=due or today - timedelta(days=5),
        total=Decimal("100.00"), paid=Decimal("0.00"), outstanding=Decimal("100.00"),
        currency="USD", status=status, disputed=False, source=source, provider_payload={},
        material_version=1,
    )


def test_feature_history_is_cut_off_at_scoring_date_and_ignores_later_settlement():
    target_issue = date(2026, 2, 1)
    target = {"id": "target", "client_id": "c", "issue_date": target_issue, "due_date": target_issue + timedelta(days=30), "total": 200, "outstanding": 200, "currency": "USD"}
    historical = {"id": "prior", "client_id": "c", "issue_date": date(2025, 1, 1), "due_date": date(2025, 1, 31), "total": 100, "outstanding": 0, "currency": "USD", "status": "PAID", "settled_at": date(2026, 1, 30)}
    current = features(target, date(2026, 1, 15), [historical], [target, historical])
    later = features(target, date(2026, 2, 5), [historical], [target, historical])
    assert current["history_count"] == 0
    assert later["history_count"] == 1


def test_feature_inputs_are_point_in_time_and_cold_start_is_shrunk():
    as_of = date(2026, 6, 1)
    invoice = {
        "id": "feature-target", "client_id": "client", "issue_date": as_of - timedelta(days=20),
        "due_date": as_of + timedelta(days=10), "total": 4000, "outstanding": 3600,
        "paid": 400, "partial_amount": 400, "partial_at": as_of - timedelta(days=2),
        "viewed_at": as_of - timedelta(days=1), "payment_term_type": "NET_30", "currency": "USD",
        "reminder_events": [
            {"sent_at": (as_of - timedelta(days=4)).isoformat()},
            {"sent_at": (as_of + timedelta(days=1)).isoformat()},
        ],
    }
    current = features(invoice, as_of, [], [invoice])
    prior = features(invoice, as_of - timedelta(days=5), [], [invoice])
    assert current["prior_paid_invoice_count"] == 0
    assert current["history_missing"] == 1
    assert current["history_late_rate"] == pytest.approx(0.45)
    assert current["viewed_by_recipient"] == 1
    assert current["reminder_count"] == 1
    assert current["term_net_30"] == 1
    assert current["partial_share"] == pytest.approx(0.1)
    assert current["days_since_last_partial_payment"] == 2
    assert prior["viewed_by_recipient"] == 0
    assert prior["reminder_count"] == 0
    assert prior["partial_share"] == 0


def test_exposure_from_synthetic_records_uses_as_of_timeline_not_final_balance():
    target = {"id": "target", "client_id": "c", "issue_date": date(2026, 1, 1), "due_date": date(2026, 1, 20), "total": 100, "outstanding": 100, "currency": "EUR"}
    other = {"id": "other", "client_id": "c", "issue_date": date(2025, 12, 1), "due_date": date(2025, 12, 20), "total": 100, "outstanding": 0, "status": "PAID", "settled_at": date(2026, 1, 10), "outcome_paid_at": date(2026, 1, 10), "cancelled_at": None, "partial_at": None, "partial_amount": Decimal("0")}
    before = features(target, date(2026, 1, 5), [], [target, other])
    after = features(target, date(2026, 1, 15), [], [target, other])
    assert before["other_open_exposure_log"] == pytest.approx(__import__("math").log1p(100))
    assert after["other_open_exposure_log"] == 0


def test_duration_censors_open_and_cancelled_rows_and_survival_curve_is_monotone():
    open_row = {"id": "open", "issue_date": date(2026, 1, 1), "outcome_paid_at": None, "settled_at": None, "cancelled": False}
    duration, event = _duration(open_row, date(2026, 2, 10))
    assert (duration, event) == (40, 0)
    _, _, subjects = __import__("ml.train", fromlist=["make_risk_rows"]).make_risk_rows(
        [open_row | {"client_id": "c", "due_date": date(2026, 1, 30), "total": 100, "outstanding": 100, "currency": "USD"},
         {**open_row, "id": "cancelled", "client_id": "c", "cancelled": True}], date(2026, 2, 10), lambda _: True,
    )
    assert len(subjects) == 1
    curve = _survival_from_hazards([0.1, 0.2, 0.0])
    assert curve[0] == 1
    assert all(0 <= item <= 1 for item in curve)
    assert all(curve[index] >= curve[index + 1] for index in range(len(curve) - 1))
    assert km_hazards(subjects)[0] == 0


def test_drift_cold_start_and_theil_sen_evidence():
    assert theil_sen_slope([0, 3, 8, 9]) == pytest.approx(3.0)
    thin = client_drift([], date.today())
    assert thin["available"] is False
    assert thin["settled_count"] == 0
    assert thin["signal"] == "insufficient_history"
    today = date.today()
    records = [{"status": "PAID", "issue_date": today - timedelta(days=(6 - index) * 30),
                "settled_at": today - timedelta(days=(6 - index) * 30),
                "due_date": today - timedelta(days=(6 - index) * 30 + index * 4)} for index in range(6)]
    drift = client_drift(records, today, cadence_days=30)
    assert drift["available"] is True
    assert drift["signal_type"] in {"paying_later", "later_than_usual"}
    assert drift["signal_strength"] > 0


def test_drift_requires_four_settlements_and_uses_client_cadence_for_quiet_signal():
    today = date(2026, 11, 10)
    sparse = [{"status": "PAID", "issue_date": today - timedelta(days=30 * index),
               "settled_at": today - timedelta(days=30 * index),
               "due_date": today - timedelta(days=30 * index)} for index in range(3)]
    result = client_drift(sparse, today, cadence_days=30)
    assert result["available"] is False
    assert result["signal_type"] == "insufficient_history"

    seven_day_client = [{"status": "PAID", "issue_date": today - timedelta(days=16 + 7 * index),
                         "settled_at": today - timedelta(days=16 + 7 * index),
                         "due_date": today - timedelta(days=16 + 7 * index)} for index in range(3)]
    quiet = client_drift(seven_day_client, today, cadence_days=7, activity_probability=0.2)
    assert quiet["signal_type"] == "gone_quiet"
    assert quiet["days_since_last_invoice"] == 16


def test_bgnbd_activity_and_drift_backtest_are_bounded_and_compare_recency():
    rows = generate_records(observation_cutoff=date(2026, 11, 10))
    by_client: dict[str, list[dict]] = {}
    for row in rows:
        by_client.setdefault(row["client_id"], []).append(row)
    activity = fit_bgnbd({key: [row["issue_date"] for row in values] for key, values in by_client.items()}, date(2026, 11, 10))
    assert activity["available"] is True
    assert activity["model"] == "BG/NBD"
    assert all(0 <= probability <= 1 for probability in activity["probabilities"].values())
    report = backtest_drift(rows, date(2026, 11, 10))
    assert report["outcome_horizon_days"] == 90
    assert report["flagged_client_count"] == report["recency_only_flagged_client_count"]
    assert report["precision"] is not None
    assert report["recency_only_precision"] is not None


def test_policy_wait_friendly_firm_and_closed_currencies_are_safe():
    today = date.today()
    invoice = SimpleNamespace(disputed=False, status="SENT", outstanding=Decimal("50.00"),
                              due_date=today + timedelta(days=2), reminder_step=0, partial_payment_allowed=False)
    assert decide(invoice, 0.2, today=today, probability_14d=0.2)["action"] == "friendly"
    assert decide(invoice, 0.8, today=today, probability_14d=0.2)["action"] == "wait"
    invoice.due_date = today - timedelta(days=10)
    assert decide(invoice, 0, today=today, probability_14d=0.2)["action"] == "friendly"
    invoice.reminder_step = 1
    assert decide(invoice, 0, today=today, probability_14d=0.2)["action"] == "firm"
    invoice.status = "PAID"
    assert decide(invoice, 0.0, today=today)["action"] == "wait"


def test_policy_ladder_cooldown_payment_pause_and_cash_gain():
    today = date(2026, 11, 10)
    invoice = SimpleNamespace(
        id="ladder", disputed=False, status="OVERDUE", outstanding=Decimal("3000.00"),
        due_date=today - timedelta(days=21), reminder_step=2, partial_payment_allowed=False,
    )
    partial = decide(invoice, 0, today=today, probability_14d=0.25, has_partial_history=True)
    assert partial["action"] == "offer_partial_payment"
    assert partial["probability_if_wait"] == 0.25
    assert partial["probability_if_sent"] == 0.35
    assert partial["expected_cash_gain"] == 300

    invoice.due_date = today - timedelta(days=45)
    invoice.reminder_step = 3
    call = decide(invoice, 0, today=today, probability_14d=0.25)
    assert call["action"] == "personal_call"

    recent_reminder = datetime(2026, 11, 7, tzinfo=timezone.utc)
    cooled = decide(invoice, 0, last_attempt_at=recent_reminder, last_attempt_tone="friendly",
                    today=today, probability_14d=0.25)
    assert cooled["action"] == "wait"
    recent_payment = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    paid_recently = decide(invoice, 0, today=today, probability_14d=0.25, last_payment_at=recent_payment)
    assert paid_recently["action"] == "wait"


def test_signed_operator_session_rejects_tampering(api):
    client, _ = api
    token = create_session(main.settings, now=int(datetime.now(timezone.utc).timestamp()))
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/auth/me").json()["authenticated"] is True
    client.cookies.set(COOKIE_NAME, token + "x")
    assert client.get("/auth/me").json()["authenticated"] is False


def test_production_refuses_default_operator_credentials(api, monkeypatch):
    client, _ = api
    insecure = Settings(_env_file=None, app_env="production")
    monkeypatch.setattr(main, "settings", insecure)
    client.cookies.set(COOKIE_NAME, create_session(insecure))
    assert client.get("/auth/me").json()["authenticated"] is False
    assert client.post("/auth/login", json={"username": "operator", "password": "change-this-local-password"}).status_code == 401


def test_public_routes_only_expose_demo_and_demo_payment_partial_then_full(api):
    client, Session = api
    with Session() as db:
        demo_client = Client(id="demo-client", display_name="Demo Buyer", source="demo", drift={})
        live_client = Client(id="live-client", display_name="Sandbox Buyer", source="paypal", drift={})
        db.add_all([demo_client, live_client])
        db.flush()
        demo = _invoice("demo-one", demo_client.id)
        live = _invoice("live-one", live_client.id, source="paypal")
        db.add_all([demo, live])
        db.commit()
    response = client.get("/invoices")
    assert response.status_code == 200
    assert [row["source"] for row in response.json()["items"]] == ["demo"]
    assert response.json()["ready_for_review_count"] == 0
    with Session() as db:
        assert db.query(PolicyDecisionLog).count() == 1
    assert client.get("/invoices/live-one").status_code == 401
    partial = client.post("/demo/invoices/demo-one/payment", json={"amount": "30.00"})
    assert partial.status_code == 200
    assert partial.json()["status"] == "PARTIALLY_PAID"
    full = client.post("/demo/invoices/demo-one/payment", json={})
    assert full.status_code == 200
    assert full.json()["status"] == "PAID"
    assert full.json()["outstanding"] == "0.00"


def test_cancelled_invoice_with_stale_balance_is_closed_in_api_and_payment_is_blocked(api):
    client, Session = api
    with Session() as db:
        owner = Client(id="cancelled-owner", display_name="Cancelled", source="demo", drift={})
        db.add(owner)
        db.flush()
        invoice = _invoice("cancelled-stale", owner.id, status="CANCELLED")
        db.add(invoice)
        db.commit()

    portfolio = client.get("/invoices?view=all").json()
    assert portfolio["items"][0]["outstanding"] == "0.00"
    assert portfolio["currency_totals"] == {}
    detail = client.get("/invoices/cancelled-stale").json()
    assert detail["outstanding"] == "0.00"
    assert detail["forecast"] is None
    assert detail["decision"]["action"] == "wait"
    assert client.post("/demo/invoices/cancelled-stale/payment", json={}).status_code == 409


def test_invoice_grid_paginates_filters_aggregates_and_sorts_on_the_server(api):
    client, Session = api
    with Session() as db:
        owners = [Client(id=f"grid-owner-{index}", display_name=f"Grid Client {index}", source="demo", drift={}) for index in range(2)]
        db.add_all(owners)
        db.flush()
        invoices = []
        for index in range(36):
            owner = owners[index % 2]
            invoice = _invoice(f"grid-{index:02}", owner.id, status="PAID" if index >= 32 else "OVERDUE")
            invoice.invoice_number = f"LP-GRID-{index:02}"
            invoice.outstanding = Decimal("0.00") if invoice.status == "PAID" else Decimal("100.00")
            invoices.append(invoice)
        db.add_all(invoices)
        db.commit()

    page = client.get("/invoices?view=all&page=2&page_size=25&sort=invoice").json()
    assert page["count"] == 36
    assert page["page"] == 2 and page["page_count"] == 2 and len(page["items"]) == 11
    assert page["view_counts"]["open"] == 32
    assert page["view_counts"]["paid"] == 4
    assert page["aging_buckets"]["late_1_30"]["count"] == 32

    overdue = client.get("/invoices?view=overdue&page_size=50").json()
    assert overdue["count"] == 32
    paid = client.get("/invoices?view=paid&page_size=50").json()
    assert paid["count"] == 4
    assert paid["currency_totals"] == {}

    filtered = client.get(f"/invoices?view=open&client_id={owners[0].id}&page_size=50").json()
    assert filtered["count"] == 16
    assert filtered["currency_totals"] == {"USD": "1600.00"}
    assert isinstance(filtered["expected_cash_by_week"], list)

    sorted_by_probability = client.get("/invoices?view=open&page_size=50&sort=p14&direction=desc").json()
    probabilities = [row["forecast"]["probabilities"]["14d"] for row in sorted_by_probability["items"]]
    assert probabilities == sorted(probabilities, reverse=True)


def test_new_client_forecast_marks_limited_history_and_reports_model_metadata(api):
    client, Session = api
    with Session() as db:
        owner = Client(id="cold-start-owner", display_name="New Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        db.add(_invoice("cold-start-invoice", owner.id))
        db.commit()

    forecast = client.get("/invoices/cold-start-invoice").json()["forecast"]
    assert forecast["limited_history"] is True
    assert forecast["history_count"] == 0
    assert forecast["model_metadata"]["training_seed"] == 20261110
    assert forecast["model_metadata"]["training_date_range"]
    assert forecast["model_metadata"]["feature_list"]


def test_evaluation_api_exposes_survival_metrics_and_feature_importance(api):
    client, _ = api
    response = client.get("/evaluation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert set(payload["survival_metrics"]) == {"lightgbm", "kaplan_meier", "cox"}
    assert "uno_concordance" in payload["survival_metrics"]["lightgbm"]
    assert payload["feature_importance"]
    assert "feature" in payload["feature_importance"][0]


def test_demo_send_is_idempotent_and_does_not_call_paypal(api, monkeypatch):
    client, Session = api
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": 0.1, "probabilities": {"14d": 0.1},
    })
    with Session() as db:
        owner = Client(id="demo-owner", display_name="Demo", source="demo", drift={})
        db.add(owner)
        db.flush()
        invoice = _invoice("demo-send", owner.id)
        db.add(invoice)
        db.flush()
        recommendation = Recommendation(invoice_id=invoice.id, action="firm", tone="firm", reason="overdue",
                                        draft_text="Hello buyer", edited_text="Hello buyer", invoice_version=1, state="proposed")
        db.add(recommendation)
        db.commit()
        recommendation_id = recommendation.id
    path = f"/demo/recommendations/{recommendation_id}/send"
    first = client.post(path, json={"text": "Approved reminder"})
    second = client.post(path, json={"text": "Approved reminder"})
    assert first.status_code == 200, first.text
    assert first.json()["simulated"] is True
    assert second.status_code == 409


def test_invalid_webhook_is_stored_but_never_mutates_invoice(api, monkeypatch):
    client, Session = api
    with Session() as db:
        owner = Client(id="webhook-owner", display_name="Sandbox", source="paypal", drift={})
        db.add(owner)
        db.flush()
        invoice = _invoice("webhook-invoice", owner.id, source="paypal")
        db.add(invoice)
        db.commit()

    class InvalidWebhookAdapter:
        def verify_webhook(self, headers, payload):
            return False

    monkeypatch.setattr(main, "PayPalAdapter", InvalidWebhookAdapter)
    response = client.post("/webhooks/paypal", json={"id": "evt-invalid", "event_type": "INVOICING.INVOICE.PAID", "resource": {"id": "webhook-invoice"}})
    assert response.status_code == 401
    with Session() as db:
        invoice = db.get(Invoice, "webhook-invoice")
        event = db.query(InvoiceEvent).filter_by(provider_event_id="evt-invalid").one()
        assert invoice.status == "OVERDUE" and invoice.outstanding == Decimal("100.00")
        assert event.verified is False and event.processing_status == "rejected"


def test_paypal_marked_as_paid_normalizes_to_closed_paid_state():
    invoice = normalize_invoice({
        "id": "paypal-marked-paid", "status": "MARKED_AS_PAID",
        "detail": {"invoice_number": "LP-MARKED-PAID", "invoice_date": date.today().isoformat()},
        "amount": {"currency_code": "USD", "value": "1.00"},
        "due_amount": {"currency_code": "USD", "value": "0.00"},
        "primary_recipients": [{"billing_info": {"email_address": "buyer@example.test"}}],
    })
    assert invoice["status"] == "PAID"
    assert invoice["paid"] == Decimal("1.00")
    assert invoice["outstanding"] == Decimal("0.00")


def test_ai_draft_rejects_unverified_numbers_and_composes_transaction_facts(monkeypatch):
    facts = {
        "invoice": {"client_name": "Avery", "invoice_number": "LP-204", "outstanding": "125.00",
                    "currency": "EUR", "due_date": "2026-11-10", "payment_url": None},
        "tone": "friendly", "policy_reason": "Friendly follow-up", "forecast_reasons": ["Usual pattern"],
        "client_signal": {}, "client_history": {"settled_invoice_count": 6},
    }
    monkeypatch.setattr(ai_service, "_gemini_json", lambda _: {"body": "Please pay by 2026-11-12."})
    assert ai_service.generate_reminder_body(facts) is None

    monkeypatch.setattr(ai_service, "_gemini_json", lambda _: {"body": "I hope things are going well. Please review this invoice when convenient."})
    draft, source = ai_service.generate_reminder_draft(facts, "Fallback")
    assert source == "gemini"
    assert draft.startswith("Hello Avery,\nI hope things are going well.")
    assert "EUR 125.00" in draft and "LP-204" in draft and "2026-11-10" in draft


def test_reply_classifier_extracts_promise_dates_and_flags_disputes(monkeypatch):
    monkeypatch.setattr(ai_service, "_gemini_json", lambda _: None)
    today = date(2026, 11, 10)
    label, classifier = ai_service.classify_reply("We will pay Friday.")
    assert (label, classifier) == ("promise_to_pay", "rules")
    assert ai_service.extract_promised_date("We will pay Friday.", today) == date(2026, 11, 13)
    assert ai_service.classify_reply("We dispute this invoice.")[0] == "dispute"
    assert ai_service.classify_reply("Could you send a receipt?")[0] == "question"


def test_reply_triage_pauses_promised_invoice_and_dispute(api, monkeypatch):
    client, Session = api
    fixed_today = date(2026, 11, 10)
    monkeypatch.setattr(main, "_today", lambda db=None, source=None: fixed_today)
    monkeypatch.setattr(main, "classify_reply", lambda reply: ("promise_to_pay", "rules") if "Friday" in reply else ("dispute", "rules"))
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": 0.1, "probabilities": {"14d": 0.1}, "reasons": [],
        "quantiles": {"p50": None}, "curve": [], "model": "test", "model_version": "test",
    })
    with Session() as db:
        owner = Client(id="reply-owner", display_name="Reply Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        db.add(_invoice("reply-invoice", owner.id))
        db.commit()

    promised = client.post("/invoices/reply-invoice/reply", json={"reply": "We will pay Friday."})
    assert promised.status_code == 200, promised.text
    assert promised.json()["classification"] == "promise_to_pay"
    assert promised.json()["promised_payment_date"] == "2026-11-13"
    assert promised.json()["reminders_paused"] is True
    detail = client.get("/invoices/reply-invoice").json()
    assert detail["decision"]["action"] == "wait"
    assert "promised payment" in detail["decision"]["reason"]

    disputed = client.post("/invoices/reply-invoice/reply", json={"reply": "We dispute this."})
    assert disputed.status_code == 200
    assert disputed.json()["classification"] == "dispute"
    assert disputed.json()["reminders_paused"] is True
    with Session() as db:
        invoice = db.get(Invoice, "reply-invoice")
        assert invoice.disputed is True
        assert db.query(ReplyTriage).filter_by(invoice_id=invoice.id).count() == 2


def test_facts_endpoints_and_receivables_questions_are_structured_and_scoped(api, monkeypatch):
    client, Session = api
    monkeypatch.setattr(main, "_today", lambda db=None, source=None: date(2026, 11, 10))
    monkeypatch.setattr(main, "_top_policy_candidates", lambda db, source=None: set())
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": 0.3, "probabilities": {"14d": 0.4},
        "quantiles": {"p10": None, "p50": {"date": "2026-11-13", "days": 3}, "p90": None},
        "reasons": ["Recent client payment history is limited."], "curve": [],
        "model": "test", "model_version": "test", "already_overdue": True,
    })
    with Session() as db:
        owner = Client(id="facts-owner", display_name="Facts Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        db.add(_invoice("facts-invoice", owner.id))
        db.commit()

    invoice_facts = client.get("/facts/invoices/facts-invoice")
    assert invoice_facts.status_code == 200
    assert invoice_facts.json()["forecast"]["model_version"] == "test"
    assert invoice_facts.json()["client"]["history"]["invoice_count"] == 1
    client_facts_response = client.get("/facts/clients/facts-owner")
    assert client_facts_response.status_code == 200
    assert client_facts_response.json()["invoices"][0]["invoice_number"] == "LP-facts-invoice"
    brief = client.get("/facts/morning-brief")
    assert brief.status_code == 200
    assert brief.json()["as_of"] == "2026-11-10"
    answer = client.post("/ask", json={"question": "Who owes me the most in USD?"})
    assert answer.status_code == 200
    assert answer.json()["intent"] == "largest_client_balance"
    assert answer.json()["data"]["client_name"] == "Facts Client"
    unsupported = client.post("/ask", json={"question": "Tell me what I should do next year."})
    assert unsupported.json()["intent"] == "unsupported"


def test_reminder_draft_endpoint_persists_model_and_fallback_audit(api, monkeypatch):
    client, Session = api
    monkeypatch.setattr(main, "_top_policy_candidates", lambda db, source=None: {"draft-invoice"})
    monkeypatch.setattr(main, "holdback_assignment", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": 0.1, "probabilities": {"14d": 0.1}, "reasons": ["A grounded forecast reason."],
        "quantiles": {"p10": None, "p50": None, "p90": None}, "curve": [],
        "model": "test", "model_version": "test", "already_overdue": True,
    })
    monkeypatch.setattr(main, "generate_reminder_draft", lambda facts, fallback: ("AI generated draft", "gemini"))
    with Session() as db:
        owner = Client(id="draft-owner", display_name="Draft Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        db.add(_invoice("draft-invoice", owner.id))
        db.commit()
    detail = client.get("/invoices/draft-invoice")
    assert detail.status_code == 200 and detail.json()["recommendation"]["draft_source"] == "template"
    generated = client.post("/invoices/draft-invoice/draft")
    assert generated.status_code == 200, generated.text
    assert generated.json()["recommendation"]["draft_text"] == "AI generated draft"
    assert generated.json()["recommendation"]["draft_source"] == "gemini"
    with Session() as db:
        recommendation = db.query(Recommendation).filter_by(invoice_id="draft-invoice").one()
        assert recommendation.draft_text == "AI generated draft"
        assert recommendation.approved_text is None


def test_paid_invoice_detail_shows_issue_forecast_actual_outcome_and_timeline(api, monkeypatch):
    client, Session = api
    monkeypatch.setattr(main, "_today", lambda db=None, source=None: date(2026, 11, 10))
    with Session() as db:
        owner = Client(id="outcome-owner", display_name="Outcome Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        invoice = _invoice("outcome-invoice", owner.id, status="PAID")
        invoice.issue_date = date(2026, 10, 1)
        invoice.due_date = date(2026, 10, 10)
        invoice.total = invoice.paid = Decimal("100.00")
        invoice.outstanding = Decimal("0.00")
        invoice.viewed_at = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        invoice.reminder_events = [{"sent_at": "2026-10-11T12:00:00+00:00", "tone": "friendly"}]
        db.add(invoice)
        db.flush()
        db.add_all([
            Payment(provider_payment_id="demo-partial-outcome", invoice_id=invoice.id, amount=Decimal("40.00"), currency="USD", occurred_at=datetime(2026, 10, 12, 12, tzinfo=timezone.utc), source="demo"),
            Payment(provider_payment_id="demo-settlement-outcome", invoice_id=invoice.id, amount=Decimal("60.00"), currency="USD", occurred_at=datetime(2026, 10, 15, 12, tzinfo=timezone.utc), source="demo"),
        ])
        db.add(Prediction(
            invoice_id=invoice.id, scoring_date=invoice.issue_date, fingerprint="issue-score",
            model_version="issue-model-v1", feature_schema_version="latepayer-features-v2",
            curve=[1.0, 0.5], quantiles={"p10": {"date": "2026-10-12"}, "p50": {"date": "2026-10-14"}, "p90": {"date": "2026-10-20"}},
            probabilities={"14d": 0.6}, reasons=[],
        ))
        db.commit()

    response = client.get("/invoices/outcome-invoice")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["outcome"]["paid_at"] == "2026-10-15"
    assert payload["outcome"]["days_from_due"] == 5
    assert payload["outcome"]["issue_forecast"]["p50_date"] == "2026-10-14"
    assert payload["outcome"]["issue_forecast"]["absolute_error_days"] == 1
    kinds = [event["kind"] for event in payload["timeline"]]
    assert {"Issued", "Sent", "Viewed by recipient", "Reminder sent", "Partial payment", "Paid"}.issubset(kinds)


def test_paid_invoice_without_payment_date_reports_unavailable(api):
    client, Session = api
    with Session() as db:
        owner = Client(id="no-paid-date-owner", display_name="No Date", source="demo", drift={})
        db.add(owner)
        db.flush()
        invoice = _invoice("no-paid-date", owner.id, status="PAID")
        invoice.paid = invoice.total
        invoice.outstanding = Decimal("0.00")
        db.add(invoice)
        db.commit()

    payload = client.get("/invoices/no-paid-date").json()
    assert payload["outcome"]["paid_at"] is None
    assert payload["outcome"]["timing"] == "date_unavailable"


def test_stale_debt_routes_to_collections_review_without_another_reminder():
    today = date(2026, 11, 10)
    invoice = _invoice("stale-debt", "client", due=today - timedelta(days=90))
    result = decide(invoice, 0.1, today=today, probability_14d=0.1)
    assert result["action"] == "review_for_writeoff_or_collections"
    assert result["stale_debt"] is True
    assert "write-off or collections" in result["reason"]


def test_paypal_cancelled_invoice_never_keeps_a_due_balance():
    invoice = normalize_invoice({
        "id": "paypal-cancelled", "status": "CANCELLED",
        "detail": {"invoice_number": "LP-CANCELLED", "invoice_date": date.today().isoformat()},
        "amount": {"currency_code": "GBP", "value": "4198.00"},
        "due_amount": {"currency_code": "GBP", "value": "4198.00"},
        "primary_recipients": [{"billing_info": {"email_address": "buyer@example.test"}}],
    })
    assert invoice["status"] == "CANCELLED"
    assert invoice["paid"] == Decimal("0.00")
    assert invoice["outstanding"] == Decimal("0.00")


def test_verified_nested_paypal_invoice_event_refreshes_invoice(api, monkeypatch):
    client, Session = api
    with Session() as db:
        owner = Client(id="nested-webhook-owner", display_name="Sandbox", source="paypal", drift={})
        db.add(owner)
        db.flush()
        db.add(_invoice("nested-webhook-invoice", owner.id, source="paypal"))
        db.commit()

    class ValidWebhookAdapter:
        def verify_webhook(self, headers, payload):
            return True

    refreshed_ids = []

    def refresh_invoice(db, adapter, paypal_id):
        refreshed_ids.append(paypal_id)
        invoice = db.get(Invoice, "nested-webhook-invoice")
        invoice.status = "PAID"
        invoice.paid = invoice.total
        invoice.outstanding = Decimal("0.00")
        db.commit()
        return invoice

    monkeypatch.setattr(main, "PayPalAdapter", ValidWebhookAdapter)
    monkeypatch.setattr(main, "refresh_paypal_invoice", refresh_invoice)
    response = client.post("/webhooks/paypal", json={
        "id": "evt-nested-invoice",
        "event_type": "INVOICING.INVOICE.PAID",
        "resource": {"invoice": {"id": "nested-webhook-invoice", "status": "MARKED_AS_PAID"}},
    })
    assert response.status_code == 200
    assert refreshed_ids == ["nested-webhook-invoice"]
    with Session() as db:
        invoice = db.get(Invoice, "nested-webhook-invoice")
        event = db.query(InvoiceEvent).filter_by(provider_event_id="evt-nested-invoice").one()
        assert invoice.status == "PAID" and invoice.outstanding == Decimal("0.00")
        assert event.verified is True and event.processing_status == "processed"


def test_sync_retry_refreshes_nested_paypal_invoice_event(api, monkeypatch):
    _, Session = api
    with Session() as db:
        event = InvoiceEvent(
            provider_event_id="evt-nested-retry", event_type="INVOICING.INVOICE.PAID",
            raw_payload={"resource": {"invoice": {"id": "paypal-nested-id"}}},
            verified=True, processing_status="retry",
        )
        db.add(event)
        db.commit()

    refreshed_ids = []

    def refresh_invoice(db, adapter, paypal_id):
        refreshed_ids.append(paypal_id)

    monkeypatch.setattr(sync_service, "refresh_paypal_invoice", refresh_invoice)
    with Session() as db:
        succeeded, failed = sync_service.retry_event_refreshes(db, object())
        db.commit()
        event = db.query(InvoiceEvent).filter_by(provider_event_id="evt-nested-retry").one()
        assert event.processing_status == "processed"
    assert (succeeded, failed) == (1, 0)
    assert refreshed_ids == ["paypal-nested-id"]


def test_paypal_mutation_timeout_is_marked_ambiguous():
    observed_accept = []

    def handler(request: httpx.Request):
        if request.url.path.endswith("/v1/oauth2/token"):
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 900})
        observed_accept.append(request.headers.get("accept"))
        raise httpx.ReadTimeout("connection closed after dispatch", request=request)

    settings = Settings(paypal_client_id="client", paypal_client_secret="secret", paypal_mode="sandbox")
    adapter = PayPalAdapter(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PayPalError) as error:
        adapter.send_invoice("invoice-id")
    assert error.value.ambiguous is True
    assert observed_accept == ["application/json"]


def test_paypal_mutation_error_keeps_provider_issue_code():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/v1/oauth2/token"):
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 900})
        return httpx.Response(406, json={
            "name": "MEDIA_TYPE_NOT_ACCEPTABLE",
            "message": "Request was rejected.",
            "details": [{"issue": "INR_FOREIGN_CURRENCY_BLOCKED"}],
        })

    settings = Settings(paypal_client_id="client", paypal_client_secret="secret", paypal_mode="sandbox")
    adapter = PayPalAdapter(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(PayPalError, match="INR_FOREIGN_CURRENCY_BLOCKED"):
        adapter.send_invoice("invoice-id")


def test_training_generator_has_current_as_of_age_mix_profiles_and_cadence():
    as_of = date(2026, 11, 10)
    first = generate_records(observation_cutoff=as_of)
    second = generate_records(observation_cutoff=as_of)
    assert first == second
    clients = {row["client_id"]: row for row in first}
    assert len(clients) == 52
    assert 1500 <= len(first) <= 3000
    assert {row["profile"] for row in first} == {"prompt", "due_date", "chronic_late", "deteriorating", "erratic"}
    assert sum(row["profile"] == "deteriorating" for row in clients.values()) == 4
    open_rows = [row for row in first if row["outstanding"] > 0 and not row["cancelled"]]
    assert len(open_rows) == 50
    assert {name: sum(row["open_age_bucket"] == name for row in open_rows) for name in OPEN_BUCKET_COUNTS} == OPEN_BUCKET_COUNTS
    assert sum(row["client_churned"] for row in clients.values()) == 3
    assert all(row["issue_date"] <= as_of for row in first)
    assert all(row["viewed_at"] is None or row["viewed_at"] <= as_of for row in first)
    assert all(isinstance(row["partial_payment_allowed"], bool) and isinstance(row["reminder_events"], list) for row in first)
