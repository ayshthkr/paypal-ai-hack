"""Restore the synthetic fixture from a durable snapshot without model fitting."""
import json
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import Date, DateTime, Numeric, bindparam, delete, insert, select, update
from app.models import ActionAttempt, Client, CollectionReview, DemoState, Invoice, Payment, PolicyDecisionLog, Prediction, ReadModel, Recommendation, ReplyTriage

SNAPSHOT_VERSION = "task3-v5"


def _json(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(type(value).__name__)


def capture(db):
    state = db.get(DemoState, "demo")
    if not state:
        return
    invoice_ids = select(Invoice.id).where(Invoice.source == "demo")
    scopes = [(Client, Client.source == "demo"), (Invoice, Invoice.source == "demo"),
              (Payment, Payment.invoice_id.in_(invoice_ids)), (Prediction, Prediction.invoice_id.in_(invoice_ids)),
              (ReadModel, ((ReadModel.key.like("forecast:demo-%") | ReadModel.key.like("forecast_summary:demo-%") | ReadModel.key.like("drift:demo-%") | (ReadModel.key == "activity:demo")) & (ReadModel.as_of == state.as_of_date)))]
    payload = {}
    for model, where in scopes:
        rows = db.execute(select(model.__table__).where(where)).mappings().all()
        payload[model.__tablename__] = json.loads(json.dumps([dict(row) for row in rows], default=_json))
    db.merge(ReadModel(key="demo:snapshot", as_of=state.as_of_date, version=SNAPSHOT_VERSION, payload=payload))
    db.commit()


def restore(db, as_of):
    snapshot = db.get(ReadModel, ("demo:snapshot", as_of))
    if not snapshot or snapshot.version != SNAPSHOT_VERSION:
        return None
    payload = snapshot.payload
    fast = _restore_changed(db, as_of, payload)
    if fast is not None:
        return fast
    invoice_ids = select(Invoice.id).where(Invoice.source == "demo")
    rec_ids = select(Recommendation.id).where(Recommendation.invoice_id.in_(invoice_ids))
    db.execute(delete(ActionAttempt).where(ActionAttempt.recommendation_id.in_(rec_ids)))
    db.execute(delete(CollectionReview).where(CollectionReview.invoice_id.in_(invoice_ids)))
    for model in (Recommendation, PolicyDecisionLog, ReplyTriage, Prediction, Payment):
        db.execute(delete(model).where(model.invoice_id.in_(invoice_ids)))
    db.execute(delete(Invoice).where(Invoice.source == "demo"))
    db.execute(delete(Client).where(Client.source == "demo"))
    db.execute(delete(ReadModel).where(ReadModel.key.like("forecast:demo-%") | ReadModel.key.like("forecast_summary:demo-%") | ReadModel.key.like("drift:demo-%") | (ReadModel.key == "activity:demo")))
    for model in (Client, Invoice, Payment, Prediction, ReadModel):
        rows = []
        for source in payload[model.__tablename__]:
            row = dict(source)
            for column in model.__table__.columns:
                value = row.get(column.name)
                if value is None:
                    continue
                if isinstance(column.type, DateTime):
                    row[column.name] = datetime.fromisoformat(value)
                elif isinstance(column.type, Date):
                    row[column.name] = date.fromisoformat(value)
                elif isinstance(column.type, Numeric):
                    row[column.name] = Decimal(value)
            rows.append(row)
        if rows:
            db.execute(insert(model.__table__), rows)
    db.commit()
    from app.http_cache import invalidate
    invalidate()
    return {"cutoff": as_of.isoformat(), "clients": len(payload["clients"]), "invoices": len(payload["invoices"]),
            "open_count": sum(float(row["outstanding"]) > 0 and row["status"] not in {"CANCELLED", "VOIDED", "REFUNDED", "DELETED", "DRAFT"} for row in payload["invoices"])}


# Public demo mutations append simulated payments and change these fields.
# Historical invoices and seed payments are immutable through the API.
_MUTABLE_FACTS = ("material_version", "reminder_step", "status", "paid", "outstanding",
                  "disputed", "promised_payment_date", "partial_payment_allowed")


def _decoded(model, source):
    row = dict(source)
    for column in model.__table__.columns:
        value = row.get(column.name)
        if value is None:
            continue
        if isinstance(column.type, DateTime):
            row[column.name] = datetime.fromisoformat(value)
        elif isinstance(column.type, Date):
            row[column.name] = date.fromisoformat(value)
        elif isinstance(column.type, Numeric):
            row[column.name] = Decimal(value)
    return row


def _restore_changed(db, as_of, payload):
    baseline = {row["id"]: row for row in payload["invoices"]}
    current = db.execute(select(Invoice.id, Invoice.client_id, *[getattr(Invoice, key) for key in _MUTABLE_FACTS])
                         .where(Invoice.source == "demo")).mappings().all()
    client_ids = set(db.scalars(select(Client.id).where(Client.source == "demo")))
    if {row["id"] for row in current} != set(baseline) or client_ids != {row["id"] for row in payload["clients"]}:
        return None  # A fixture/schema change needs the full snapshot restoration.
    def comparable(value):
        return _json(value) if isinstance(value, (date, datetime, Decimal)) else value
    changed = [baseline[row["id"]] for row in current
               if any(comparable(row[key]) != baseline[row["id"]].get(key) for key in _MUTABLE_FACTS)]
    clients = {row["client_id"] for row in changed}
    client_invoice_ids = {row["id"] for row in payload["invoices"] if row["client_id"] in clients}
    invoice_ids = select(Invoice.id).where(Invoice.source == "demo")
    rec_ids = select(Recommendation.id).where(Recommendation.invoice_id.in_(invoice_ids))
    db.execute(delete(ActionAttempt).where(ActionAttempt.recommendation_id.in_(rec_ids)))
    for model in (Recommendation, PolicyDecisionLog, ReplyTriage):
        db.execute(delete(model).where(model.invoice_id.in_(invoice_ids)))
    db.execute(delete(Payment).where(Payment.invoice_id.in_(invoice_ids),
                                     Payment.provider_payment_id.like("demo-payment-%")))
    for model, sources in ((Invoice, changed), (Client, [row for row in payload["clients"] if row["id"] in clients])):
        values = []
        for source in sources:
            row = _decoded(model, source)
            row["restore_id"] = row.pop("id")
            values.append(row)
        if values:
            db.execute(update(model.__table__).where(model.__table__.c.id == bindparam("restore_id")), values)
    if clients:
        db.execute(delete(CollectionReview).where(CollectionReview.invoice_id.in_(client_invoice_ids)))
        db.execute(delete(Prediction).where(Prediction.invoice_id.in_(client_invoice_ids)))
        predictions = [_decoded(Prediction, row) for row in payload["predictions"] if row["invoice_id"] in client_invoice_ids]
        if predictions:
            db.execute(insert(Prediction.__table__), predictions)
        keys = {f"{prefix}:{identifier}" for identifier in client_invoice_ids for prefix in ("forecast", "forecast_summary")}
        keys.update(f"drift:{identifier}" for identifier in clients)
        db.execute(delete(ReadModel).where(ReadModel.key.in_(keys)))
        projections = [_decoded(ReadModel, row) for row in payload["read_models"] if row["key"] in keys]
        if projections:
            db.execute(insert(ReadModel.__table__), projections)
    db.commit()
    from app.http_cache import invalidate
    invalidate()
    return {"cutoff": as_of.isoformat(), "clients": len(payload["clients"]), "invoices": len(payload["invoices"]),
            "open_count": sum(float(row["outstanding"]) > 0 and row["status"] not in {"CANCELLED", "VOIDED", "REFUNDED", "DELETED", "DRAFT"} for row in payload["invoices"])}
