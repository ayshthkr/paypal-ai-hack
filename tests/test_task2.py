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


def test_pagination_filters_export_and_cache_contracts(api):
    import csv
    import io
    from decimal import Decimal
    from app.models import Client, Invoice
    client, Session = api
    today = date.today()
    with Session() as db:
        db.add(Client(id="contract-client", display_name="Contract", source="demo", drift={}))
        db.flush()
        for index in range(31):
            db.add(Invoice(id=f"contract-{index}", invoice_number=f"TEST-{index:03}", client_id="contract-client",
                issue_date=today-timedelta(days=30), due_date=today+timedelta(days=index-15), total=Decimal(100),
                paid=Decimal(0), outstanding=Decimal(100), currency="USD", status="SENT", source="demo", material_version=1))
        db.commit()
    for size in (0, 1000):
        assert client.get(f"/invoices?page_size={size}").status_code == 422
    page = client.get('/portfolio?page_size=25').json()
    assert page['count'] == 31 and page['page_count'] == 2 and len(page['items']) == 25
    assert 'curve' not in page['items'][0]['forecast']
    assert client.get('/invoices?page=999').json()['items'] == []
    assert client.get('/invoices?q=not-a-match').json()['count'] == 0
    assert client.get('/invoices/does-not-exist').status_code == 404
    assert client.get('/facts/clients/does-not-exist').status_code == 404
    assert client.get('/invoices?statuses=PAID').json()['count'] == 0
    assert client.get(f'/invoices?due_from={today.isoformat()}').json()['count'] == 16
    rows = list(csv.reader(io.StringIO(client.get('/exports/invoices.csv').text)))
    assert len(rows) == 32 and rows[0][-3:] == ['p10','p50','p90']
    first = client.get('/clients')
    assert first.headers['cache-control'] == 'private, max-age=30, stale-while-revalidate=120'
    assert client.get('/clients', headers={'If-None-Match':first.headers['etag']}).status_code == 304
    detail = client.get('/invoices/contract-0').json()
    assert 'curve' in detail['forecast']
    assert client.post('/demo/invoices/contract-0/payment', json={}).status_code == 200
    assert client.get('/invoices/contract-0').json()['status'] == 'PAID'
    with Session() as db:
        db.get(Invoice, 'contract-30').disputed = True
        db.commit()
    all_page = client.get('/invoices?view=all&page_size=1&sort=balance').json()
    assert all_page['count'] == 31 and len(all_page['items']) == 1
    assert all_page['currency_totals']['USD'] == '3000.00'
    assert client.get('/invoices?view=open').json()['count'] == 29
    paid_export = list(csv.reader(io.StringIO(client.get('/exports/invoices.csv?view=paid').text)))
    assert paid_export[1][-3:] == ['', '', '']


def test_seeded_read_budgets_snapshot_restore_and_no_scoring(api, monkeypatch, request):
    from time import perf_counter
    from scripts import seed_demo
    from app.services import forecast
    from app.services.demo_snapshot import restore
    from app.models import Invoice
    from app.http_cache import invalidate
    client, Session = api
    monkeypatch.setattr(seed_demo, 'SessionLocal', Session)
    result = seed_demo.seed(date.today())
    assert result['expected_drifting_clients'] == 8
    from sqlalchemy import event
    from app.models import ReadModel
    clock_queries = []
    def reject_snapshot(target, context):
        assert target.key != "demo:snapshot", "A read loaded the reset snapshot"
    def count_clock(conn, cursor, statement, parameters, context, executemany):
        if "FROM demo_state" in statement:
            clock_queries.append(statement)
    engine = Session.kw["bind"]
    event.listen(ReadModel, "load", reject_snapshot)
    event.listen(engine, "before_cursor_execute", count_clock)
    def remove_listeners():
        if event.contains(ReadModel, "load", reject_snapshot):
            event.remove(ReadModel, "load", reject_snapshot)
        if event.contains(engine, "before_cursor_execute", count_clock):
            event.remove(engine, "before_cursor_execute", count_clock)
    request.addfinalizer(remove_listeners)
    # Projection existence is tested by making every estimator unavailable.
    load_artifacts = forecast._load_artifacts
    monkeypatch.setattr(forecast, '_load_artifacts', lambda: (_ for _ in ()).throw(AssertionError('Scoring on a read')))
    for path in ['/portfolio', '/invoices?view=all', '/clients', '/action-queue', '/facts/clients/demo-client-01']:
        invalidate()
        clock_queries.clear()
        start = perf_counter()
        response = client.get(path)
        assert response.status_code == 200, response.text[:200]
        assert perf_counter()-start < 2, path
        assert len(clock_queries) <= 2, (path, len(clock_queries))
    invoice_id = client.get('/invoices').json()['items'][0]['id']
    invalidate()
    start = perf_counter()
    assert client.get(f'/invoices/{invoice_id}').status_code == 200
    assert perf_counter()-start < .5
    remove_listeners()
    monkeypatch.setattr(forecast, '_load_artifacts', load_artifacts)
    with Session() as db:
        row = db.get(Invoice, invoice_id)
        original_balance = row.outstanding
        row.outstanding = 0
        row.status = 'PAID'
        db.commit()
    from scripts import rescore_demo
    monkeypatch.setattr(rescore_demo, 'SessionLocal', Session)
    assert rescore_demo.rescore_and_report(validate_seed=False)['open_count'] == 49
    with Session() as db:
        start = perf_counter()
        restored = restore(db, date.today())
        assert perf_counter()-start < 5
        assert restored['open_count'] == 50
    with Session() as db:
        assert db.get(Invoice, invoice_id).outstanding == original_balance


def test_skip_undo_and_draft_persistence(api):
    from decimal import Decimal
    from app.models import Client, Invoice, Recommendation
    client, Session = api
    today = date.today()
    with Session() as db:
        db.add(Client(id='draft-owner',display_name='Draft owner',source='demo',drift={}))
        db.flush()
        db.add(Invoice(id='draft-invoice',invoice_number='DRAFT-1',client_id='draft-owner',issue_date=today-timedelta(days=40),
            due_date=today-timedelta(days=10),total=Decimal(100),paid=Decimal(0),outstanding=Decimal(100),currency='USD',status='OVERDUE',source='demo',material_version=1))
        db.flush()
        db.add(Recommendation(id='draft-rec',invoice_id='draft-invoice',action='friendly',tone='friendly',reason='Review',
            draft_text='Original draft',edited_text='Original draft',invoice_version=1,state='proposed'))
        db.commit()
    assert client.post('/recommendations/draft-rec/draft',json={'text':'Saved human edit'}).status_code == 200
    assert client.post('/recommendations/draft-rec/skip').status_code == 200
    assert client.post('/recommendations/draft-rec/undo-skip').status_code == 200
    with Session() as db:
        row = db.get(Recommendation,'draft-rec')
        assert row.state == 'proposed' and row.edited_text == 'Saved human edit'
        assert db.get(Invoice,'draft-invoice').reminder_step == 0


def test_repeated_queue_reads_reuse_one_proposal_and_one_audit(api):
    from decimal import Decimal
    from app.models import Client, Invoice, Recommendation, PolicyDecisionLog, DemoState
    from app.services.policy import holdback_assignment
    from app.http_cache import invalidate
    client, Session = api
    today = date.today()
    invoice_id = next(f'queue-race-{i}' for i in range(100) if not holdback_assignment(f'queue-race-{i}',today))
    with Session() as db:
        db.add(DemoState(id='demo', as_of_date=today))
        db.add(Client(id='queue-race-owner',display_name='Queue race',source='demo',drift={}))
        db.flush()
        db.add(Invoice(id=invoice_id,invoice_number='QR-1',client_id='queue-race-owner',issue_date=today-timedelta(days=33),
            due_date=today-timedelta(days=3),total=Decimal(100),paid=Decimal(0),outstanding=Decimal(100),currency='USD',status='OVERDUE',source='demo',material_version=1))
        db.commit()
    client.get('/portfolio')
    with Session() as db:
        assert db.query(PolicyDecisionLog).count() == 0
    first = client.get('/action-queue')
    invalidate()
    second = client.get('/action-queue')
    assert first.status_code == second.status_code == 200
    recommendation_id = first.json()['items'][0]['recommendation']['id']
    assert recommendation_id == second.json()['items'][0]['recommendation']['id']
    assert client.post(f'/recommendations/{recommendation_id}/skip').status_code == 200
    skipped = client.get('/action-queue').json()
    assert skipped['count'] == 0 and skipped['skipped_count'] == 1
    assert client.post(f'/recommendations/{recommendation_id}/undo-skip').status_code == 200
    assert client.get('/action-queue').json()['count'] == 1
    with Session() as db:
        assert db.query(Recommendation).filter_by(invoice_id=invoice_id,state='proposed').count() == 1
        assert db.query(PolicyDecisionLog).filter_by(invoice_id=invoice_id).count() == 1


def test_utc_audit_timestamps_use_the_merchant_payment_date(api, monkeypatch):
    from datetime import datetime, timezone
    from decimal import Decimal
    from app.models import Client, DemoState, Invoice, Payment
    from app.services import dates
    from app.services.forecast import _as_record
    monkeypatch.setattr(dates, 'get_settings', lambda: SimpleNamespace(merchant_timezone='Asia/Kolkata'))
    client, Session = api
    paid_at = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
    with Session() as db:
        db.add(Client(id='calendar-client',display_name='Calendar',source='demo',drift={}))
        db.add(DemoState(id='demo',as_of_date=date(2026,11,11)))
        db.flush()
        invoice = Invoice(id='calendar-invoice',invoice_number='CAL-1',client_id='calendar-client',issue_date=date(2026,10,11),
            due_date=date(2026,11,11),total=Decimal(100),paid=Decimal(100),outstanding=Decimal(0),currency='USD',status='PAID',source='demo',material_version=1,
            viewed_at=paid_at,reminder_events=[{'sent_at':paid_at.isoformat(),'tone':'friendly'}])
        payment = Payment(invoice_id=invoice.id,provider_payment_id='calendar-payment',amount=Decimal(100),currency='USD',occurred_at=paid_at,source='demo')
        db.add(invoice)
        db.flush()
        db.add(payment)
        db.commit()
        record = _as_record(invoice,[payment])
        assert record['settled_at'] == record['viewed_at'] == date(2026,11,11)
        assert record['reminder_events'][0]['sent_at'] == '2026-11-11'
    outcome = client.get('/invoices/calendar-invoice').json()['outcome']
    assert outcome['paid_at'] == '2026-11-11' and outcome['days_from_due'] == 0
