from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app import main
from app.config import Settings
from app.models import Client, Invoice, InvoiceEvent, Recommendation
from app.security import COOKIE_NAME, create_session
from app.services.drift import client_drift, theil_sen_slope
from app.services.paypal import PayPalAdapter, PayPalError, normalize_invoice
from app.services.policy import decide
from ml.features import features
from ml.synthetic import generate_records
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
    today = date.today()
    records = [{"status": "PAID", "settled_at": today - timedelta(days=(5 - index) * 30),
                "due_date": today - timedelta(days=(5 - index) * 30 + index * 4)} for index in range(5)]
    drift = client_drift(records, today, cadence_days=30)
    assert drift["available"] is True
    assert drift["signal"] in {"increasing_delay", "recent_late"}


def test_policy_wait_friendly_firm_and_closed_currencies_are_safe():
    today = date.today()
    invoice = SimpleNamespace(disputed=False, status="SENT", outstanding=Decimal("50.00"), due_date=today + timedelta(days=2))
    assert decide(invoice, 0.2, today=today)["action"] == "friendly"
    assert decide(invoice, 0.8, today=today)["action"] == "wait"
    invoice.due_date = today - timedelta(days=1)
    assert decide(invoice, None, today=today)["action"] == "firm"
    invoice.status = "PAID"
    assert decide(invoice, 0.0, today=today)["action"] == "wait"


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
    assert client.get("/invoices/live-one").status_code == 401
    partial = client.post("/demo/invoices/demo-one/payment", json={"amount": "30.00"})
    assert partial.status_code == 200
    assert partial.json()["status"] == "PARTIALLY_PAID"
    full = client.post("/demo/invoices/demo-one/payment", json={})
    assert full.status_code == 200
    assert full.json()["status"] == "PAID"
    assert full.json()["outstanding"] == "0.00"


def test_demo_send_is_idempotent_and_does_not_call_paypal(api):
    client, Session = api
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
    assert first.status_code == 200 and first.json()["simulated"] is True
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


def test_training_generator_has_exact_reproducible_size_and_profiles():
    first = generate_records()
    second = generate_records()
    assert len(first) == 400 and first == second
    assert {row["profile"] for row in first} == {"prompt", "slow", "volatile", "drifting", "new"}
