from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app import main
from app import security
from app.config import Settings
from app.models import AIAudit, Client, DemoState, Invoice, InvoiceEvent, OperatorSession, Payment, PolicyDecisionLog, Prediction, ReadModel, Recommendation, ReplyTriage
from app.security import COOKIE_NAME, create_session, session_claims
from app.services.drift import backtest_drift, client_drift, theil_sen_slope
from app.services.paypal import PayPalAdapter, PayPalError, normalize_invoice, payment_records
from app.services.forecast import _as_record
from app.services.policy import decide
from app.services import sync as sync_service
from app.services import ai as ai_service
from ml.features import features
from ml.synthetic import OPEN_BUCKET_COUNTS, generate_records
from ml.activity import fit_bgnbd
from ml.evaluation import coverage_by_issue_month
from ml.train import _duration, _survival_from_hazards, km_hazards
from scripts import seed_demo_if_empty


def _invoice(invoice_id: str, client_id: str, *, source="demo", status="OVERDUE", due=None):
    today = date.today()
    return Invoice(
        id=invoice_id, invoice_number=f"LP-{invoice_id}", client_id=client_id,
        issue_date=today - timedelta(days=30), due_date=due or today - timedelta(days=5),
        total=Decimal("100.00"), paid=Decimal("0.00"), outstanding=Decimal("100.00"),
        currency="USD", status=status, disputed=False, source=source, provider_payload={},
        material_version=1,
    )


def _set_operator_cookie(client, Session, settings=None, now=None):
    settings = settings or main.settings
    issued_at = int(now if now is not None else datetime.now(timezone.utc).timestamp())
    token = create_session(settings, now=issued_at)
    username, token_issued_at, session_id = session_claims(token, settings, now=issued_at)
    with Session() as db:
        db.add(OperatorSession(
            id=session_id, username=username, issued_at=token_issued_at,
            expires_at=token_issued_at + settings.session_max_age_seconds,
        ))
        db.commit()
    client.cookies.set(COOKIE_NAME, token)
    return token


def test_forecast_summary_projection_preserves_weekly_cash_mass(api, monkeypatch):
    from app.services import forecast, projections

    _, Session = api
    as_of = date(2026, 11, 11)
    weekly = [{"week_start": "2026-11-09", "probability": 0.24}]
    payload = {
        "quantiles": {}, "probabilities": {}, "probability_by_due": 0.0,
        "weekly_settlement_probabilities": weekly, "model": "test", "model_version": "test-v1",
        "limited_history": True, "inference_revision": forecast.FORECAST_REVISION,
        "artifact_revision": "test-artifact",
    }
    monkeypatch.setattr(main, "_prepare_portfolio_context", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_activity_for_source", lambda *args, **kwargs: {})
    monkeypatch.setattr(main, "_drift_for", lambda *args, **kwargs: {})
    monkeypatch.setattr(main, "_today", lambda *args, **kwargs: as_of)
    monkeypatch.setattr(forecast, "forecast_invoice", lambda *args, **kwargs: payload)

    with Session() as db:
        owner = Client(id="weekly-summary-owner", display_name="Weekly Summary", source="demo", drift={})
        invoice = _invoice("weekly-summary-invoice", owner.id)
        db.add_all([owner, invoice])
        db.flush()
        projections.refresh(db, {owner.id}, as_of=as_of)

        summary = db.get(ReadModel, (f"forecast_summary:{invoice.id}", as_of))
        assert summary is not None
        assert summary.payload["weekly_settlement_probabilities"] == weekly


def test_deployed_brier_includes_known_payments_inside_horizon():
    from ml.train import _conditional_ipcw_brier_term

    curve = [1.0] + [0.5] * 180
    censor_survival = [1.0] * 181
    paid_within_horizon = _conditional_ipcw_brier_term(
        curve, event=1, remaining=5, age=30, horizon=14, censor_g=censor_survival,
    )
    right_censored_before_horizon = _conditional_ipcw_brier_term(
        curve, event=0, remaining=5, age=30, horizon=14, censor_g=censor_survival,
    )
    paid_after_horizon = _conditional_ipcw_brier_term(
        curve, event=1, remaining=20, age=30, horizon=14, censor_g=censor_survival,
    )

    assert paid_within_horizon == pytest.approx(0.25)
    assert right_censored_before_horizon is None
    assert paid_after_horizon == pytest.approx(0.25)


def test_deployed_replay_freezes_outcomes_after_score_date():
    from ml.train import _records_as_of

    score_date = date(2026, 11, 11)
    row = {
        "id": "point-in-time", "client_id": "pit-client", "issue_date": date(2026, 9, 1),
        "due_date": date(2026, 10, 1), "total": Decimal("100.00"), "paid": Decimal("20.00"),
        "outstanding": Decimal("80.00"), "partial_at": date(2026, 11, 1),
        "partial_amount": Decimal("20.00"), "outcome_paid_at": date(2026, 12, 1),
        "settled_at": None, "viewed_at": date(2026, 11, 20),
        "reminder_events": [
            {"sent_at": "2026-11-05T12:00:00+00:00"},
            {"sent_at": "2026-11-20T12:00:00+00:00"},
        ],
    }

    snapshot = _records_as_of([row], score_date)[0]

    assert snapshot["settled_at"] is None
    assert snapshot["paid"] == Decimal("20.00")
    assert snapshot["outstanding"] == Decimal("80.00")
    assert snapshot["viewed_at"] is None
    assert len(snapshot["reminder_events"]) == 1
    assert "outcome_paid_at" not in snapshot


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
        "partial_payment_allowed": True,
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
    assert current["partial_payment_allowed"] == 1
    assert current["partial_share"] == pytest.approx(0.1)
    assert current["days_since_last_partial_payment"] == 2
    assert prior["viewed_by_recipient"] == 0
    assert prior["reminder_count"] == 0
    assert prior["partial_share"] == 0


def test_reminder_recency_ignores_events_after_scoring_timestamp():
    as_of = date(2026, 6, 1)
    invoice = {
        "id": "reminder-cutoff", "client_id": "client", "issue_date": as_of - timedelta(days=40),
        "due_date": as_of - timedelta(days=10), "total": 500, "outstanding": 500,
        "reminder_events": [
            {"sent_at": (as_of - timedelta(days=5)).isoformat()},
            {"sent_at": (as_of + timedelta(days=1)).isoformat()},
        ],
    }
    values = features(invoice, as_of, [], [invoice])
    assert values["reminder_count"] == 1
    assert values["days_since_last_reminder"] == 5


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


def test_monthly_coverage_uses_only_invoices_with_complete_interval_bounds():
    report = coverage_by_issue_month([
        {"issue_date": "2026-08-01", "coverage_interval_lower_date": "2026-08-02", "coverage_interval_upper_date": "2026-08-10", "within_interval": True},
        {"issue_date": "2026-08-02", "coverage_interval_lower_date": "2026-08-03", "coverage_interval_upper_date": "2026-08-11", "within_interval": False},
        {"issue_date": "2026-08-03", "coverage_interval_lower_date": "2026-08-04", "coverage_interval_upper_date": None, "within_interval": False},
    ])
    assert report == [{
        "month": "2026-08", "paid_invoice_count": 3, "eligible_interval_count": 2,
        "excluded_count": 1, "coverage": 0.5,
    }]


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
    unresolved = decide(invoice, 0, unresolved=True, today=today, probability_14d=0.2)
    assert unresolved["action"] == "wait"
    assert "unresolved" in unresolved["reason"]
    invoice.status = "PAID"
    assert decide(invoice, 0.0, today=today)["action"] == "wait"


def test_reminder_cash_uplift_scales_with_forecast_probability():
    today = date(2026, 11, 10)
    invoice = SimpleNamespace(disputed=False, status="OVERDUE", outstanding=Decimal("1000"),
                              currency="USD", due_date=today-timedelta(days=3), reminder_step=0,
                              partial_payment_allowed=False)
    low = decide(invoice, .1, today=today, probability_14d=.1)
    high = decide(invoice, .1, today=today, probability_14d=.4)
    assert low["expected_cash_gain"] == 90
    assert high["expected_cash_gain"] == 60
    assert low["reminder_uplift_assumption"] == .1


def test_policy_ladder_cooldown_payment_pause_and_cash_gain():
    today = date(2026, 11, 10)
    invoice = SimpleNamespace(
        id="ladder", disputed=False, status="OVERDUE", outstanding=Decimal("3000.00"),
        due_date=today - timedelta(days=21), reminder_step=2, partial_payment_allowed=False,
    )
    partial = decide(invoice, 0, today=today, probability_14d=0.25, has_partial_history=True)
    assert partial["action"] == "offer_partial_payment"
    assert partial["probability_if_wait"] == 0.25
    assert partial["probability_if_sent"] == 0.325
    assert partial["expected_cash_gain"] == 225

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
    client, Session = api
    token = _set_operator_cookie(client, Session)
    assert client.get("/auth/me").json()["authenticated"] is True
    client.cookies.set(COOKIE_NAME, token + "x")
    assert client.get("/auth/me").json()["authenticated"] is False


def test_operator_session_expires_at_configured_age(api, monkeypatch):
    client, Session = api
    settings = Settings(
        _env_file=None, app_env="production", session_secret="e" * 48,
        operator_username="operator-test", operator_password="password-for-test-only",
        session_max_age_seconds=120,
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    expired_at = int(datetime.now(timezone.utc).timestamp()) - settings.session_max_age_seconds - 1
    _set_operator_cookie(client, Session, settings, now=expired_at)
    assert client.get("/auth/me").json()["authenticated"] is False


def test_logout_revokes_operator_session_server_side(api):
    client, Session = api
    token = _set_operator_cookie(client, Session)
    assert client.get("/auth/me").json()["authenticated"] is True
    assert client.post("/auth/logout").status_code == 200
    client.cookies.set(COOKIE_NAME, token)
    assert client.get("/auth/me").json()["authenticated"] is False
    assert client.get("/sync/status").status_code == 401


def test_login_limits_failed_attempts_per_ip(api, monkeypatch):
    client, _ = api
    with main._AUTH_LOCK:
        main._AUTH_FAILURES.clear()
    monkeypatch.setattr(main, "_verify_login", lambda username, password: False)
    for _ in range(5):
        assert client.post("/auth/login", json={"username": "wrong", "password": "wrong"}).status_code == 401
    limited = client.post("/auth/login", json={"username": "wrong", "password": "wrong"})
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


def test_login_failure_message_is_neutral_for_unknown_user_and_wrong_password(api, monkeypatch):
    client, _ = api
    with main._AUTH_LOCK:
        main._AUTH_FAILURES.clear()
    monkeypatch.setattr(
        main, "_verify_login",
        lambda username, password: username == "operator-test" and password == "correct-password",
    )
    unknown_user = client.post("/auth/login", json={"username": "unknown-user", "password": "anything"})
    wrong_password = client.post("/auth/login", json={"username": "operator-test", "password": "wrong"})
    assert unknown_user.status_code == wrong_password.status_code == 401
    assert unknown_user.json()["detail"] == wrong_password.json()["detail"] == "Username or password is incorrect."


def test_production_refuses_default_operator_credentials(api, monkeypatch):
    client, _ = api
    insecure = Settings(_env_file=None, app_env="production")
    monkeypatch.setattr(main, "settings", insecure)
    client.cookies.set(COOKIE_NAME, create_session(insecure))
    assert client.get("/auth/me").json()["authenticated"] is False
    assert client.post("/auth/login", json={"username": "operator", "password": "change-this-local-password"}).status_code == 401


def test_operator_paypal_and_sync_routes_require_a_valid_session(api):
    client, _ = api
    assert client.post("/sync").status_code == 401
    assert client.get("/sync/status").status_code == 401
    body = {"client_id": "missing", "amount": "10.00", "payment_term_type": "NET_15"}
    assert client.post("/paypal/invoices", json=body, headers={"Idempotency-Key": "d2fe94452cb34ecf9b9a591f00000002"}).status_code == 401
    assert client.post("/paypal/invoices", json=body).status_code == 401
    assert client.post("/recommendations/missing/send", json={"text": "review"}).status_code == 401
    assert client.post("/recommendations/missing/approve-partial-payment").status_code == 401


def test_operator_cannot_create_invoice_for_recipient_outside_allowlist(api, monkeypatch):
    client, Session = api
    settings = Settings(
        _env_file=None, app_env="production", session_secret="s" * 48,
        operator_username="operator-test", operator_password="password-for-test-only",
        paypal_mode="sandbox", paypal_allowed_recipient_emails="allowed@example.test",
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    _set_operator_cookie(client, Session, settings)
    with Session() as db:
        owner = Client(
            id="not-allowlisted-owner", provider_customer_id="outside@example.test",
            display_name="Outside Buyer", email="outside@example.test", source="paypal", drift={},
        )
        db.add(owner)
        db.commit()
    response = client.post(
        "/paypal/invoices",
        json={"client_id": "not-allowlisted-owner", "amount": "1.00", "payment_term_type": "NET_15"},
        headers={"Idempotency-Key": "outside-allowlist-key-000000000000"},
    )
    assert response.status_code == 403
    assert "PAYPAL_ALLOWED_RECIPIENT_EMAILS" in response.json()["detail"]


def test_webhook_verification_calls_paypal_signature_api_with_exact_event_body():
    settings = Settings(
        _env_file=None, paypal_mode="sandbox", paypal_client_id="test-client",
        paypal_client_secret="test-secret", paypal_webhook_id="test-webhook",
    )
    event = {"id": "evt-verified", "event_type": "INVOICING.INVOICE.PAID", "resource": {"status": "PAID"}}
    expected_verification = {
        "transmission_id": "transmission-1", "transmission_time": "2026-11-11T00:00:00Z",
        "cert_url": "https://api.paypal.com/cert", "auth_algo": "SHA256withRSA",
        "transmission_sig": "signature", "webhook_id": "test-webhook", "webhook_event": event,
    }
    verification_requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 300})
        assert request.url.path == "/v1/notifications/verify-webhook-signature"
        verification_requests.append(request)
        body = json.loads(request.content)
        assert {**body, "webhook_event": event} == expected_verification
        status = "SUCCESS" if body["webhook_event"] == event else "FAILURE"
        return httpx.Response(200, json={"verification_status": status})

    headers = {
        "PayPal-Transmission-Id": "transmission-1", "PayPal-Transmission-Time": "2026-11-11T00:00:00Z",
        "PayPal-Cert-Url": "https://api.paypal.com/cert", "PayPal-Auth-Algo": "SHA256withRSA",
        "PayPal-Transmission-Sig": "signature",
    }
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        adapter = PayPalAdapter(settings, client=client)
        assert adapter.verify_webhook(headers, event) is True
        assert adapter.verify_webhook(headers, {**event, "resource": {"status": "ALTERED"}}) is False
    assert len(verification_requests) == 2


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
        assert db.query(PolicyDecisionLog).count() == 0
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


def test_mixed_currency_open_totals_remain_separate_in_api(api):
    client, Session = api
    with Session() as db:
        owner = Client(id="currency-owner", display_name="Currency Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        usd = _invoice("currency-usd", owner.id)
        eur = _invoice("currency-eur", owner.id)
        usd.currency, usd.outstanding = "USD", Decimal("120.00")
        eur.currency, eur.outstanding = "EUR", Decimal("80.00")
        db.add_all([usd, eur])
        db.commit()

    payload = client.get("/invoices?view=open&page_size=50").json()
    assert payload["currency_totals"] == {"EUR": "80.00", "USD": "120.00"}
    amounts = payload["aging_buckets"]["late_1_30"]["amounts"]
    assert amounts == {"EUR": "80.00", "USD": "120.00"}
    assert sum(bucket["count"] for bucket in payload["cash_horizon"].values()) == payload["view_counts"]["open"]
    horizon_amounts = {}
    for bucket in payload["cash_horizon"].values():
        for currency, amount in bucket["amounts"].items():
            horizon_amounts[currency] = horizon_amounts.get(currency, Decimal("0")) + Decimal(amount)
    assert horizon_amounts == {currency: Decimal(amount) for currency, amount in payload["currency_totals"].items()}
    assert "within the next 14 days" in payload["top_late_scenario"]["assumption"]


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
    assert sum(bucket["count"] for bucket in page["cash_horizon"].values()) == 32
    assert sum(bucket["count"] for bucket in page["aging_buckets"].values()) == page["view_counts"]["open"]

    overdue = client.get("/invoices?view=overdue&page_size=50").json()
    assert overdue["count"] == 32
    paid = client.get("/invoices?view=paid&page_size=50").json()
    assert paid["count"] == 4
    # The header totals describe the open book and stay stable while paging
    # historical paid rows.
    assert paid["currency_totals"] == {"USD": "3200.00"}

    filtered = client.get(f"/invoices?view=open&client_id={owners[0].id}&page_size=50").json()
    assert filtered["count"] == 16
    assert filtered["currency_totals"] == {"USD": "1600.00"}
    assert isinstance(filtered["expected_cash_by_week"], list)

    sorted_by_probability = client.get("/invoices?view=open&page_size=50&sort=p14&direction=desc").json()
    probabilities = [row["forecast"]["probabilities"]["14d"] for row in sorted_by_probability["items"]]
    assert probabilities == sorted(probabilities, reverse=True)


def test_demo_time_travel_validates_sixty_day_window_and_updates_due_math(api):
    client, Session = api
    with Session() as db:
        owner = Client(id="time-owner", display_name="Time Client", source="demo", drift={})
        db.add(owner)
        db.add(DemoState(id="demo", as_of_date=date(2026, 11, 10)))
        db.flush()
        invoice = _invoice("time-invoice", owner.id, due=date(2026, 11, 5))
        invoice.issue_date = date(2026, 10, 1)
        db.add(invoice)
        db.commit()

    current = client.get("/invoices?as_of=2026-11-10&view=open").json()
    earlier = client.get("/invoices?as_of=2026-11-01&view=open").json()
    assert current["as_of"] == "2026-11-10" and current["items"][0]["days_to_due"] == -5
    assert earlier["as_of"] == "2026-11-01" and earlier["items"][0]["days_to_due"] == 4
    assert earlier["as_of_max"] == "2026-11-10"
    assert client.get("/invoices?as_of=2026-09-10").status_code == 422
    assert client.get("/invoices?as_of=2026-11-11").status_code == 422


def test_client_what_if_changes_term_and_partial_payment_features(api, monkeypatch):
    client, Session = api
    observed = []
    def fake_forecast(db, invoice, today=None, persist=True, scenario=None):
        observed.append(scenario)
        return {"quantiles": {"p50": {"date": "2026-12-01", "days": 21}},
                "probabilities": {"14d": 0.32}, "model": "test", "limited_history": True}
    monkeypatch.setattr(main, "forecast_invoice", fake_forecast)
    with Session() as db:
        owner = Client(id="what-if-owner", display_name="What If Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        paid = _invoice("what-if-paid", owner.id, status="PAID")
        paid.total, paid.paid, paid.outstanding = Decimal("600.00"), Decimal("600.00"), Decimal("0.00")
        db.add(paid)
        db.commit()

    response = client.post("/clients/what-if-owner/what-if", json={"payment_term_type": "NET_15"})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["baseline"]["payment_term_type"] == "NET_30"
    assert payload["with_terms"]["payment_term_type"] == "NET_15"
    assert payload["with_partial_payment"]["partial_payment_allowed"] is True
    assert [row["payment_term_type"] for row in observed] == ["NET_30", "NET_15", "NET_15"]
    assert [row["partial_payment_allowed"] for row in observed] == [False, False, True]


def test_action_queue_generates_five_or_fewer_reviewable_recommendations(api, monkeypatch):
    client, Session = api
    monkeypatch.setattr(main, "holdback_assignment", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": 0.1, "probabilities": {"14d": 0.1}, "quantiles": {"p50": None},
        "reasons": ["test"], "model": "test", "model_version": "test", "already_overdue": True,
    })
    with Session() as db:
        owner = Client(id="queue-owner", display_name="Queue Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        invoice = _invoice("queue-invoice", owner.id)
        invoice.due_date = date.today() - timedelta(days=5)
        db.add(invoice)
        db.commit()

    response = client.get("/action-queue")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["recommendation"]["state"] == "proposed"
    assert payload["items"][0]["client_email"] is None
    assert payload["items"][0]["decision"]["expected_cash_gain"] > 0
    portfolio = client.get("/invoices?view=all").json()
    assert portfolio["ready_for_review_count"] == payload["pending_count"] == 1
    assert sum(bucket["count"] for bucket in portfolio["aging_buckets"].values()) == portfolio["view_counts"]["open"]
    assert sum(portfolio["drifting_signal_counts"].values()) == portfolio["drifting_client_count"]


def test_action_queue_exposes_paypal_recipient_only_to_operator(api, monkeypatch):
    client, Session = api
    settings = Settings(
        app_env="production", session_secret="x" * 48,
        operator_username="operator-test", operator_password="password-for-test-only",
    )
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "holdback_assignment", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": 0.1, "probabilities": {"14d": 0.1}, "quantiles": {"p50": None},
        "reasons": ["test"], "model": "test", "model_version": "test", "already_overdue": True,
    })
    allowed_email = "buyer@example.test"
    with Session() as db:
        owner = Client(
            id="operator-queue-owner", display_name="Sandbox Buyer", email=allowed_email,
            source="paypal", drift={},
        )
        db.add(owner)
        db.add(_invoice("operator-queue-invoice", owner.id, source="paypal", due=date.today() - timedelta(days=15)))
        db.commit()

    public = client.get("/action-queue")
    assert public.status_code == 200
    assert public.json()["items"] == []
    _set_operator_cookie(client, Session, settings)
    assert client.get("/auth/me").json()["authenticated"] is True
    operator = client.get("/action-queue")
    assert operator.status_code == 200, operator.text
    assert operator.json()["items"][0]["invoice_number"] == "LP-operator-queue-invoice"
    assert operator.json()["items"][0]["client_email"] == allowed_email


def test_currency_lane_order_and_ninety_day_collections_lane(api, monkeypatch):
    client, Session = api
    monkeypatch.setattr(main, "holdback_assignment", lambda *args, **kwargs: False)
    monkeypatch.setattr(main, "forecast_invoice", lambda db, invoice, **kwargs: {
        "probability_by_due": .1, "probabilities": {"14d": .1}, "quantiles": {"p50": None},
        "reasons": [], "model": "test", "model_version": "test", "already_overdue": True,
    })
    today = date.today()
    with Session() as db:
        db.add(DemoState(id="demo", as_of_date=today))
        owner = Client(id="multi-currency-owner", display_name="Multi Currency", source="demo", drift={})
        db.add(owner)
        db.flush()
        invoices = []
        for invoice_id, currency, amount, days_late in [
            ("currency-gbp-large", "GBP", 9000, 10),
            ("currency-gbp-small", "GBP", 1000, 10),
            ("currency-usd", "USD", 250, 10),
            ("collection-old", "USD", 12000, 90),
        ]:
            row = _invoice(invoice_id, owner.id)
            row.currency = currency
            row.total = row.outstanding = Decimal(amount)
            row.due_date = today - timedelta(days=days_late)
            invoices.append(row)
        db.add_all(invoices)
        db.commit()

    with Session() as db:
        rows = db.query(Invoice).all()
        main._prepare_portfolio_context(db, rows)
        ranked = main._rank_policy_candidates(db, "demo")
    assert ranked == ["currency-gbp-large", "currency-usd", "currency-gbp-small"]
    queue = client.get("/action-queue").json()
    assert [row["id"] for row in queue["collections_review"]] == ["collection-old"]
    assert queue["collections_review"][0]["decision"]["action"] == "review_for_writeoff_or_collections"


def test_portfolio_signal_total_equals_all_visible_signal_counts(api, monkeypatch):
    client, Session = api
    signal_types = ["paying_later", "later_than_usual", "gone_quiet", "habitually_late"]
    monkeypatch.setattr(main, "_drift_for", lambda db, owner, activity_probability=None: {
        "signal": signal_types[int(owner.id.rsplit("-", 1)[1])],
        "signal_type": signal_types[int(owner.id.rsplit("-", 1)[1])],
        "available": True, "severity_score": 1, "signal_strength": .5,
        "recent_median_delay_days": 20, "usual_delay_days": 10,
        "money_at_risk_by_currency": {"USD": 100},
    })
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": .1, "probabilities": {"14d": .1}, "quantiles": {"p50": None},
        "reasons": [], "model": "test", "model_version": "test", "already_overdue": True,
    })
    today = date.today()
    with Session() as db:
        for index in range(4):
            owner = Client(id=f"signal-owner-{index}", display_name=f"Signal {index}", source="demo", drift={})
            db.add(owner)
            db.flush()
            db.add(_invoice(f"signal-invoice-{index}", owner.id, due=today-timedelta(days=10)))
        db.commit()
    payload = client.get("/invoices?view=open").json()
    assert payload["drifting_client_count"] == 3
    assert sum(payload["drifting_signal_counts"].values()) == 3
    assert payload["habitually_late_client_count"] == 1


def test_health_probe_is_minimal_and_ready_does_not_expose_configuration(api):
    client, _ = api
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json() == {"ok": True}
    assert health.headers["x-content-type-options"] == "nosniff"
    assert health.headers["x-frame-options"] == "DENY"
    assert health.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    assert client.get("/ready").status_code == 404


def test_named_demo_stories_are_safe_when_the_synthetic_fixture_is_empty(api):
    client, _ = api
    response = client.get("/demo-stories")
    assert response.status_code == 200
    stories = response.json()["items"]
    assert {story["key"] for story in stories} == {"verdant_outlier", "dispute", "firm_reminder", "terms_shift"}
    assert all(story["href"].startswith(("/clients/", "/invoices/", "/")) for story in stories)


def test_http_logs_exclude_operator_password_session_cookie_paypal_ids_and_webhook_body(api, caplog):
    import logging

    client, _ = api
    sentinels = ["operator-password-sentinel", "session-cookie-sentinel", "paypal-invoice-id-sentinel", "webhook-body-sentinel"]
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        client.post("/auth/login", json={"username": "demo-user", "password": sentinels[0]})
        client.get("/healthz", cookies={COOKIE_NAME: sentinels[1]})
        client.post("/webhooks/paypal", json={"id": sentinels[2], "resource": {"body": sentinels[3]}})
    for value in sentinels:
        assert value not in caplog.text


def test_public_demo_reset_reseeds_only_after_rate_limit_and_preserves_cooldown(api, monkeypatch):
    client, Session = api
    with Session() as db:
        db.add(DemoState(id="demo", as_of_date=date(2026, 11, 10)))
        db.commit()
    calls = []
    monkeypatch.setattr(main, "_seed_demo", lambda as_of: calls.append(as_of) or {
        "cutoff": as_of.isoformat(), "clients": 50, "invoices": 2000, "open_count": 50,
    })

    reset = client.post("/demo/reset")
    limited = client.post("/demo/reset")
    assert reset.status_code == 200 and reset.json()["reset"] is True
    assert "PayPal Sandbox records were not changed" in reset.json()["message"]
    assert limited.status_code == 429
    assert calls == [date(2026, 11, 10)]


def test_deployment_bootstrap_replaces_legacy_demo_and_rescores(api, monkeypatch):
    _, Session = api
    with Session() as db:
        owner = Client(id="legacy-owner", display_name="Legacy", source="demo", drift={})
        db.add(owner)
        db.flush()
        db.add(_invoice("legacy-invoice", owner.id))
        db.commit()
    monkeypatch.setattr(seed_demo_if_empty, "SessionLocal", Session)
    seeded, rescored = [], []
    monkeypatch.setattr(seed_demo_if_empty, "seed", lambda: seeded.append(True) or {"clients": 50, "invoices": 2000})
    monkeypatch.setattr(seed_demo_if_empty, "rescore_and_report", lambda: rescored.append(True) or {"forecast_inside_horizon_share": 0.9})

    result = seed_demo_if_empty.seed_if_empty()
    assert result["seeded"] is True
    assert result["previous_clients"] == 1 and result["previous_invoices"] == 1
    assert seeded == [True] and rescored == [True]


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
    detail = client.get("/invoices/demo-send")
    assert detail.status_code == 200
    assert detail.json()["reminder_outcomes"][0]["status"] == "checking"


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


def test_gemini_flash_lite_request_uses_supported_generation_config(monkeypatch):
    calls = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"body":"Please review the invoice when convenient."}'}]}}]}

    def post(url, **kwargs):
        calls.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(ai_service, "get_settings", lambda: SimpleNamespace(gemini_api_key="test-key"))
    monkeypatch.setattr(ai_service.httpx, "post", post)

    result = ai_service._gemini_json_request("Use only the provided facts.")

    assert result == {"body": "Please review the invoice when convenient."}
    assert calls["url"].endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert calls["headers"] == {"x-goog-api-key": "test-key"}
    assert calls["timeout"].read == 15.0
    assert "temperature" not in calls["json"]["generationConfig"]
    assert calls["json"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "minimal"}
    assert calls["json"]["generationConfig"]["responseMimeType"] == "application/json"


@pytest.mark.parametrize("first_failure", ["service_unavailable", "timeout"])
def test_gemini_request_retries_once_after_transient_failures(monkeypatch, first_failure):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": '{"body":"Please review the invoice when convenient."}'}]}}]}

    def post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            if first_failure == "timeout":
                raise httpx.TimeoutException("temporary timeout")
            request = httpx.Request("POST", url)
            raise httpx.HTTPStatusError(
                "temporary service error", request=request, response=httpx.Response(503, request=request)
            )
        return Response()

    monkeypatch.setattr(ai_service, "get_settings", lambda: SimpleNamespace(gemini_api_key="test-key"))
    monkeypatch.setattr(ai_service.httpx, "post", post)
    monkeypatch.setattr(ai_service.time, "sleep", lambda _: None)

    assert ai_service._gemini_json_request("Use only the provided facts.") == {
        "body": "Please review the invoice when convenient."
    }
    assert len(calls) == 2


def test_reply_classifier_extracts_promise_dates_and_flags_disputes(monkeypatch):
    monkeypatch.setattr(ai_service, "_gemini_json", lambda _: None)
    today = date(2026, 11, 10)
    label, classifier = ai_service.classify_reply("We will pay Friday.")
    assert (label, classifier) == ("promise_to_pay", "rules")
    assert ai_service.extract_promised_date("We will pay Friday.", today) == date(2026, 11, 13)
    assert ai_service.classify_reply("We dispute this invoice.")[0] == "dispute"
    assert ai_service.classify_reply("Could you send a receipt?")[0] == "question"


def test_reply_triage_requires_approval_and_can_be_corrected(api, monkeypatch):
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
    assert promised.json()["state"] == "pending"
    assert promised.json()["reminders_paused"] is False
    detail = client.get("/invoices/reply-invoice").json()
    assert detail["promised_payment_date"] is None
    applied = client.post(f"/invoices/reply-invoice/reply-triage/{promised.json()['id']}/apply", json={
        "classification": "promise_to_pay", "promised_payment_date": "2026-11-13",
    })
    assert applied.status_code == 200, applied.text
    assert applied.json()["state"] == "applied"
    detail = client.get("/invoices/reply-invoice").json()
    assert detail["decision"]["action"] == "wait"
    assert "promised payment" in detail["decision"]["reason"]

    disputed = client.post("/invoices/reply-invoice/reply", json={"reply": "We dispute this."})
    assert disputed.status_code == 200
    assert disputed.json()["classification"] == "dispute"
    assert disputed.json()["reminders_paused"] is True
    with Session() as db:
        assert db.get(Invoice, "reply-invoice").disputed is False
    applied_dispute = client.post(f"/invoices/reply-invoice/reply-triage/{disputed.json()['id']}/apply", json={
        "classification": "dispute", "promised_payment_date": None,
    })
    assert applied_dispute.status_code == 200, applied_dispute.text
    with Session() as db:
        invoice = db.get(Invoice, "reply-invoice")
        assert invoice.disputed is True
        assert db.query(ReplyTriage).filter_by(invoice_id=invoice.id).count() == 2


def test_forecast_scores_hypothetical_new_invoice_dates(api, monkeypatch):
    from app.services import forecast as forecast_service

    _, Session = api
    as_of = date(2026, 11, 10)
    monkeypatch.setattr(forecast_service, "_load_artifacts", lambda: (
        None, [0.01] * 180, "kaplan_meier", "scenario-test", {}, {},
    ))
    with Session() as db:
        owner = Client(id="scenario-owner", display_name="Scenario Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        invoice = _invoice("scenario-existing-invoice", owner.id)
        invoice.issue_date = date(2026, 7, 1)
        invoice.due_date = date(2026, 7, 31)
        db.add(invoice)
        db.flush()
        result = forecast_service.forecast_invoice(db, invoice, today=as_of, persist=False, scenario={
            "issue_date": as_of,
            "due_date": as_of + timedelta(days=15),
            "payment_term_type": "NET_15",
            "outstanding": Decimal("100.00"),
            "total": Decimal("100.00"),
        })
        assert result["already_overdue"] is False
        assert result["probability_by_due"] > 0


def test_demo_sessions_keep_mutations_isolated_between_browsers(api, monkeypatch):
    client, Session = api
    session_a, session_b = str(uuid4()), str(uuid4())
    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": 0.1, "probabilities": {"14d": 0.1},
        "quantiles": {"p10": None, "p50": None, "p90": None},
        "reasons": [], "model": "test", "model_version": "test", "already_overdue": True,
    })
    monkeypatch.setattr(main, "_drift_for", lambda *args, **kwargs: {
        "signal_type": "insufficient_history", "available": False, "severity_score": 0,
    })
    today = date.today()
    with Session() as db:
        db.add(DemoState(id="demo", as_of_date=today))
        db.add(Client(id="isolated-owner", display_name="Isolated", source="demo", drift={}))
        db.flush()
        db.add(_invoice("shared-base-invoice", "isolated-owner", due=today - timedelta(days=5)))
        db.commit()

    source_a = main._ensure_demo_session(session_a)
    source_b = main._ensure_demo_session(session_b)
    assert source_a != source_b
    first = client.get("/invoices?view=open", headers={"x-demo-session": session_a})
    second = client.get("/invoices?view=open", headers={"x-demo-session": session_b})
    assert first.status_code == second.status_code == 200
    invoice_a = first.json()["items"][0]
    invoice_b = second.json()["items"][0]
    assert invoice_a["source"] == source_a and invoice_b["source"] == source_b
    assert invoice_a["id"] != invoice_b["id"]

    paid = client.post(f"/demo/invoices/{invoice_a['id']}/payment", json={}, headers={"x-demo-session": session_a})
    assert paid.status_code == 200, paid.text
    with Session() as db:
        assert db.get(Invoice, "shared-base-invoice").outstanding == Decimal("100.00")
        assert db.scalar(select(Invoice).where(Invoice.source == source_b)).outstanding == Decimal("100.00")
        assert db.scalar(select(Invoice).where(Invoice.source == source_a)).outstanding == Decimal("0.00")


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
        bigger_eur = Client(id="facts-eur-owner", display_name="EUR Balance", source="demo", drift={})
        db.add(bigger_eur)
        db.flush()
        eur_invoice = _invoice("facts-eur-invoice", bigger_eur.id)
        eur_invoice.currency = "EUR"
        eur_invoice.total = eur_invoice.outstanding = Decimal("999999.00")
        db.add(eur_invoice)
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
    eur_answer = client.post("/ask", json={"question": "Who owes me the most in EUR?"})
    assert eur_answer.json()["data"]["client_name"] == "EUR Balance"
    assert eur_answer.json()["data"]["currency"] == "EUR"
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
    assert detail.json()["recommendation"]["draft_model"] is None
    generated = client.post("/invoices/draft-invoice/draft")
    assert generated.status_code == 200, generated.text
    assert generated.json()["recommendation"]["draft_text"] == "AI generated draft"
    assert generated.json()["recommendation"]["draft_source"] == "gemini"
    assert generated.json()["recommendation"]["draft_model"] == "gemini-3.5-flash-lite"
    with Session() as db:
        recommendation = db.query(Recommendation).filter_by(invoice_id="draft-invoice").one()
        assert recommendation.draft_text == "AI generated draft"
        assert recommendation.draft_model == "gemini-3.5-flash-lite"
        assert recommendation.approved_text is None
        audit = db.query(AIAudit).filter_by(invoice_id="draft-invoice", kind="draft").one()
        assert audit.output["model"] == "gemini-3.5-flash-lite"


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
    assert payload["reminder_outcomes"][0]["status"] == "paid"
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


def test_missing_provider_payment_date_is_not_replaced_with_sync_time():
    invoice = _invoice("paid-date-missing", "client", status="PAID")
    invoice.synced_at = datetime(2026, 11, 10, tzinfo=timezone.utc)
    assert _as_record(invoice, [])["settled_at"] is None
    assert payment_records({"payments": {"transactions": [{
        "payment_id": "payment-without-time", "amount": {"currency_code": "USD", "value": "25.00"},
    }]}}) == []


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
    payload = {
        "id": "evt-nested-invoice",
        "event_type": "INVOICING.INVOICE.PAID",
        "resource": {"invoice": {"id": "nested-webhook-invoice", "status": "MARKED_AS_PAID"}},
    }
    response = client.post("/webhooks/paypal", json=payload)
    assert response.status_code == 200
    assert refreshed_ids == ["nested-webhook-invoice"]
    duplicate = client.post("/webhooks/paypal", json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json() == {"received": True, "duplicate": True, "processed": True}
    assert refreshed_ids == ["nested-webhook-invoice"]
    with Session() as db:
        invoice = db.get(Invoice, "nested-webhook-invoice")
        event = db.query(InvoiceEvent).filter_by(provider_event_id="evt-nested-invoice").one()
        assert invoice.status == "PAID" and invoice.outstanding == Decimal("0.00")
        assert event.verified is True and event.processing_status == "processed"
        assert event.raw_payload == payload


def test_processed_webhook_rejects_altered_replay_without_mutating_invoice(api, monkeypatch):
    client, Session = api
    with Session() as db:
        owner = Client(id="altered-webhook-owner", display_name="Sandbox", source="paypal", drift={})
        db.add(owner)
        db.flush()
        db.add(_invoice("altered-webhook-invoice", owner.id, source="paypal"))
        db.commit()

    class ValidPaidWebhookAdapter:
        def verify_webhook(self, headers, payload):
            return payload.get("resource", {}).get("invoice", {}).get("status") == "MARKED_AS_PAID"

    refresh_count = 0

    def refresh_invoice(db, adapter, paypal_id):
        nonlocal refresh_count
        refresh_count += 1
        invoice = db.get(Invoice, "altered-webhook-invoice")
        invoice.status = "PAID"
        invoice.paid = invoice.total
        invoice.outstanding = Decimal("0.00")
        db.commit()
        return invoice

    monkeypatch.setattr(main, "PayPalAdapter", ValidPaidWebhookAdapter)
    monkeypatch.setattr(main, "refresh_paypal_invoice", refresh_invoice)
    original = {
        "id": "evt-altered-replay",
        "event_type": "INVOICING.INVOICE.PAID",
        "resource": {"invoice": {"id": "altered-webhook-invoice", "status": "MARKED_AS_PAID"}},
    }
    assert client.post("/webhooks/paypal", json=original).status_code == 200
    altered = {
        **original,
        "resource": {"invoice": {"id": "altered-webhook-invoice", "status": "CANCELLED"}},
    }
    rejected = client.post("/webhooks/paypal", json=altered)
    assert rejected.status_code == 401
    assert refresh_count == 1
    with Session() as db:
        invoice = db.get(Invoice, "altered-webhook-invoice")
        event = db.query(InvoiceEvent).filter_by(provider_event_id="evt-altered-replay").one()
        assert invoice.status == "PAID" and invoice.outstanding == Decimal("0.00")
        assert event.verified is True and event.processing_status == "processed"
        assert event.raw_payload == original


def test_processed_webhook_rejects_differently_signed_body_for_same_event_id(api, monkeypatch):
    client, Session = api
    with Session() as db:
        owner = Client(id="signed-altered-owner", display_name="Sandbox", source="paypal", drift={})
        db.add(owner)
        db.flush()
        db.add(_invoice("signed-altered-invoice", owner.id, source="paypal"))
        db.commit()

    class ValidWebhookAdapter:
        def verify_webhook(self, headers, payload):
            return True

    refresh_count = 0

    def refresh_invoice(db, adapter, paypal_id):
        nonlocal refresh_count
        refresh_count += 1
        invoice = db.get(Invoice, "signed-altered-invoice")
        invoice.status = "PAID"
        invoice.paid = invoice.total
        invoice.outstanding = Decimal("0.00")
        db.commit()
        return invoice

    monkeypatch.setattr(main, "PayPalAdapter", ValidWebhookAdapter)
    monkeypatch.setattr(main, "refresh_paypal_invoice", refresh_invoice)
    original = {"id": "evt-signed-altered", "event_type": "INVOICING.INVOICE.PAID", "resource": {"id": "signed-altered-invoice"}}
    assert client.post("/webhooks/paypal", json=original).status_code == 200
    altered = {**original, "extra": "different but independently signed body"}
    rejected = client.post("/webhooks/paypal", json=altered)
    assert rejected.status_code == 409
    assert refresh_count == 1
    with Session() as db:
        invoice = db.get(Invoice, "signed-altered-invoice")
        event = db.query(InvoiceEvent).filter_by(provider_event_id="evt-signed-altered").one()
        assert invoice.status == "PAID" and invoice.outstanding == Decimal("0.00")
        assert event.verified is True and event.processing_status == "processed"
        assert event.raw_payload == original


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
    assert sum(row["profile"] == "deteriorating" for row in clients.values()) == 8
    open_rows = [row for row in first if row["outstanding"] > 0 and not row["cancelled"]]
    assert len(open_rows) == 50
    assert {name: sum(row["open_age_bucket"] == name for row in open_rows) for name in OPEN_BUCKET_COUNTS} == OPEN_BUCKET_COUNTS
    assert sum(row["client_churned"] for row in clients.values()) == 3
    assert all(row["issue_date"] <= as_of for row in first)
    assert all(row["viewed_at"] is None or row["viewed_at"] <= as_of for row in first)
    assert all(isinstance(row["partial_payment_allowed"], bool) and isinstance(row["reminder_events"], list) for row in first)


def test_paypal_sync_imports_view_state_terms_and_partial_payment_configuration(api):
    _, Session = api
    payload = {
        "id": "paypal-viewed-invoice", "status": "UNPAID",
        "detail": {
            "invoice_number": "LP-VIEWED-1", "invoice_date": "2026-10-01",
            "payment_term": {"term_type": "NET_15"}, "viewed_by_recipient": True,
            "metadata": {"recipient_viewed_time": "2026-10-04T12:30:00Z"},
        },
        "amount": {"currency_code": "USD", "value": "150.00"},
        "due_amount": {"currency_code": "USD", "value": "150.00"},
        "configuration": {"partial_payment": {"allow_partial_payment": True}},
        "primary_recipients": [{"billing_info": {"email_address": "viewed@example.test"}}],
    }
    with Session() as db:
        invoice, created = sync_service.upsert_paypal_invoice(db, payload)
        assert created is True
        assert invoice.due_date == date(2026, 10, 16)
        assert invoice.viewed_by_recipient is True
        assert invoice.viewed_at == datetime(2026, 10, 4, 12, 30, tzinfo=timezone.utc)
        assert invoice.partial_payment_allowed is True
        assert invoice.payment_term_type == "NET_15"


def test_forecast_cache_reuses_same_day_prediction_and_invalidates_on_invoice_change(api, monkeypatch):
    from app.services import forecast as forecast_service

    _, Session = api
    as_of = date(2026, 11, 10)
    model_calls = []

    class CountingModel:
        def predict(self, matrix, **kwargs):
            model_calls.append(len(matrix))
            return [0.01] * len(matrix)

    monkeypatch.setattr(forecast_service, "_load_artifacts", lambda: (
        CountingModel(), [0.005] * 180, "lightgbm", "cache-test-v1", {"feature_list": []}, {},
    ))
    with Session() as db:
        owner = Client(id="cache-owner", display_name="Cache Client", source="demo", drift={})
        db.add(owner)
        db.flush()
        historical = []
        for index, issue in enumerate((date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1))):
            row = _invoice(f"cache-paid-{index}", owner.id, status="PAID")
            row.issue_date = issue
            row.due_date = issue + timedelta(days=30)
            row.total = row.paid = Decimal("100.00")
            row.outstanding = Decimal("0.00")
            historical.append(row)
        target = _invoice("cache-target", owner.id)
        target.issue_date = date(2026, 10, 1)
        target.due_date = date(2026, 11, 20)
        db.add_all([*historical, target])
        db.flush()
        for index, row in enumerate(historical):
            db.add(Payment(
                provider_payment_id=f"cache-payment-{index}", invoice_id=row.id, amount=Decimal("100.00"),
                currency="USD", occurred_at=datetime.combine(row.issue_date + timedelta(days=35), datetime.min.time(), tzinfo=timezone.utc), source="demo",
            ))
        db.flush()

        first = forecast_service.forecast_invoice(db, target, today=as_of, persist=True)
        second = forecast_service.forecast_invoice(db, target, today=as_of, persist=True)
        assert first["scored_at"] == second["scored_at"]
        assert model_calls == [180]
        assert db.query(Prediction).filter_by(invoice_id=target.id, scoring_date=as_of).count() == 1

        target.partial_payment_allowed = True
        target.material_version += 1
        db.flush()
        forecast_service.forecast_invoice(db, target, today=as_of, persist=True)
        assert model_calls == [180, 180]
        assert db.query(Prediction).filter_by(invoice_id=target.id, scoring_date=as_of).count() == 2


def test_paypal_partial_payment_update_uses_full_invoice_put_and_notifies_recipient():
    put_requests = []
    current = {
        "id": "paypal-update-1", "status": "UNPAID",
        "detail": {"invoice_number": "LP-UPDATE-1", "metadata": {"create_time": "2026-10-01T00:00:00Z"}},
        "invoicer": {}, "primary_recipients": [], "items": [],
        "configuration": {"allow_tip": False}, "amount": {"currency_code": "USD", "value": "100.00"},
        "due_amount": {"currency_code": "USD", "value": "100.00"}, "payments": [], "links": [],
    }

    def handler(request: httpx.Request):
        if request.url.path.endswith("/v1/oauth2/token"):
            return httpx.Response(200, json={"access_token": "test-token", "expires_in": 900})
        if request.method == "GET":
            return httpx.Response(200, json=current)
        put_requests.append(request)
        return httpx.Response(204)

    settings = Settings(paypal_client_id="client", paypal_client_secret="secret", paypal_mode="sandbox")
    adapter = PayPalAdapter(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    adapter.enable_partial_payment("paypal-update-1")
    request = put_requests[0]
    body = __import__("json").loads(request.content)
    assert body["configuration"]["partial_payment"] == {"allow_partial_payment": True}
    assert "metadata" not in body["detail"]
    assert "amount" not in body and "status" not in body and "links" not in body
    assert request.url.params["send_to_recipient"] == "true"


def test_sync_status_records_import_failure_and_webhook_retry_count(api, monkeypatch):
    client, Session = api
    _set_operator_cookie(client, Session)
    with Session() as db:
        db.add(InvoiceEvent(
            provider_event_id="retry-status-event", event_type="INVOICING.INVOICE.PAID",
            raw_payload={}, verified=True, processing_status="retry",
        ))
        db.commit()

    class FailingAdapter:
        def list_invoices(self):
            raise PayPalError("sandbox temporarily unavailable")

    monkeypatch.setattr(main, "PayPalAdapter", FailingAdapter)
    result = client.post("/sync")
    assert result.status_code == 200
    assert result.json()["status"] == "partial"
    status = client.get("/sync/status")
    assert status.status_code == 200
    assert status.json()["last_run"]["status"] == "partial"
    assert status.json()["last_run"]["errors"]
    assert status.json()["webhook_failures"] == 1


def test_operator_can_create_and_send_allowlisted_sandbox_invoice(api, monkeypatch):
    client, Session = api
    allowed_email = "allowed@example.test"
    settings = Settings(
        app_env=main.settings.app_env, session_secret=main.settings.session_secret,
        operator_username=main.settings.operator_username, operator_password=main.settings.operator_password,
        paypal_mode="sandbox", paypal_allowed_recipient_emails=allowed_email,
        paypal_sandbox_currency="USD",
    )
    monkeypatch.setattr(main, "settings", settings)
    _set_operator_cookie(client, Session, settings)
    with Session() as db:
        owner = Client(
            id="create-invoice-owner", provider_customer_id=allowed_email, display_name="Allowed Buyer",
            email=allowed_email, source="paypal", drift={},
        )
        db.add(owner)
        db.commit()

    calls = []
    invoice_data = {
        "id": "paypal-created-1", "status": "DRAFT",
        "detail": {"invoice_number": "LP-CREATED-1", "invoice_date": date.today().isoformat(), "payment_term": {"term_type": "NET_15"}},
        "amount": {"currency_code": "USD", "value": "125.50"},
        "due_amount": {"currency_code": "USD", "value": "125.50"},
        "configuration": {"partial_payment": {"allow_partial_payment": True}},
        "primary_recipients": [{"billing_info": {"email_address": allowed_email}}],
    }

    class FakeAdapter:
        def create_invoice(self, *args, **kwargs):
            calls.append(("create", args, kwargs))
            return invoice_data

        def send_invoice(self, paypal_id, **kwargs):
            calls.append(("send", paypal_id, kwargs))
            return paypal_id

        def get_invoice(self, paypal_id):
            return {**invoice_data, "status": "UNPAID"}

    def refresh(db, adapter, paypal_id):
        invoice, _ = sync_service.upsert_paypal_invoice(db, adapter.get_invoice(paypal_id))
        return invoice

    monkeypatch.setattr(main, "PayPalAdapter", FakeAdapter)
    monkeypatch.setattr(main, "refresh_paypal_invoice", refresh)
    request_body = {
        "client_id": "create-invoice-owner", "amount": "125.50",
        "payment_term_type": "NET_15", "partial_payment_allowed": True,
    }
    headers = {"Idempotency-Key": "d2fe94452cb34ecf9b9a591f00000001"}
    response = client.post("/paypal/invoices", json=request_body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "UNPAID"
    assert [call[0] for call in calls] == ["create", "send"]
    assert calls[0][1][0] == allowed_email
    assert calls[0][2]["payment_term_type"] == "NET_15"
    assert calls[0][2]["partial_payment_allowed"] is True
    assert calls[0][2]["request_id"] == headers["Idempotency-Key"]
    assert calls[1][2]["request_id"] == f"{headers['Idempotency-Key']}-send"
    retry = client.post("/paypal/invoices", json=request_body, headers=headers)
    assert retry.status_code == 200 and retry.json() == response.json()
    assert [call[0] for call in calls] == ["create", "send"]
    conflict = client.post("/paypal/invoices", json={**request_body, "amount": "130.00"}, headers=headers)
    assert conflict.status_code == 409
    with Session() as db:
        invoice = db.query(Invoice).filter_by(paypal_id="paypal-created-1").one()
        assert invoice.source == "paypal" and invoice.partial_payment_allowed is True


def test_operator_partial_payment_approval_updates_provider_then_resyncs(api, monkeypatch):
    client, Session = api
    settings = Settings(
        app_env=main.settings.app_env, session_secret=main.settings.session_secret,
        operator_username=main.settings.operator_username, operator_password=main.settings.operator_password,
        paypal_mode="sandbox", paypal_allowed_recipient_emails="partial@example.test",
    )
    monkeypatch.setattr(main, "settings", settings)
    _set_operator_cookie(client, Session, settings)
    today = date(2026, 11, 10)
    monkeypatch.setattr(main, "_today", lambda db=None, source=None: today)
    monkeypatch.setattr(main, "forecast_invoice", lambda *args, **kwargs: {
        "probability_by_due": 0.2, "probabilities": {"14d": 0.2},
    })
    monkeypatch.setattr(main, "_client_has_partial_history", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "_top_policy_candidates", lambda *args, **kwargs: {"partial-approval-invoice"})
    monkeypatch.setattr(main, "holdback_assignment", lambda *args, **kwargs: False)
    with Session() as db:
        owner = Client(
            id="partial-approval-owner", provider_customer_id="partial@example.test", display_name="Partial Buyer",
            email="partial@example.test", source="paypal", drift={},
        )
        db.add(owner)
        db.flush()
        invoice = _invoice("partial-approval-invoice", owner.id, source="paypal", due=today - timedelta(days=21))
        invoice.paypal_id = "paypal-partial-approval"
        invoice.invoice_number = "LP-PARTIAL-APPROVAL"
        invoice.total = invoice.outstanding = Decimal("5000.00")
        invoice.material_version = 1
        invoice.reminder_step = 2
        db.add(invoice)
        db.flush()
        recommendation = Recommendation(
            invoice_id=invoice.id, action="offer_partial_payment", tone="friendly", reason="Large balance with partial history.",
            draft_text="Enable partial payment.", draft_source="policy", edited_text="Enable partial payment.",
            invoice_version=invoice.material_version, policy_step=3, state="proposed",
        )
        db.add(recommendation)
        db.commit()
        recommendation_id = recommendation.id

    refreshes = []
    updates = []

    class FakeAdapter:
        def enable_partial_payment(self, paypal_id):
            updates.append(paypal_id)

    def refresh(db, adapter, paypal_id):
        refreshes.append(paypal_id)
        invoice = db.get(Invoice, "partial-approval-invoice")
        if len(refreshes) == 2:
            invoice.partial_payment_allowed = True
            invoice.material_version += 1
            db.flush()
        return invoice

    monkeypatch.setattr(main, "PayPalAdapter", FakeAdapter)
    monkeypatch.setattr(main, "refresh_paypal_invoice", refresh)
    response = client.post(f"/recommendations/{recommendation_id}/approve-partial-payment")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "applied"
    assert updates == ["paypal-partial-approval"]
    assert refreshes == ["paypal-partial-approval", "paypal-partial-approval"]
    with Session() as db:
        invoice = db.get(Invoice, "partial-approval-invoice")
        saved = db.get(Recommendation, recommendation_id)
        assert invoice.partial_payment_allowed is True
        assert saved.state == "applied"
