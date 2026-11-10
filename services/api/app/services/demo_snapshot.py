"""Restore the synthetic fixture from a durable snapshot without model fitting."""
import json
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import Date, DateTime, Numeric, delete, insert, select
from app.models import ActionAttempt, Client, DemoState, Invoice, Payment, PolicyDecisionLog, Prediction, ReadModel, Recommendation, ReplyTriage

SNAPSHOT_VERSION = "task2-v4"


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
              (ReadModel, ((ReadModel.key.like("forecast:demo-%") | ReadModel.key.like("drift:demo-%") | (ReadModel.key == "activity:demo")) & (ReadModel.as_of == state.as_of_date)))]
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
    invoice_ids = select(Invoice.id).where(Invoice.source == "demo")
    rec_ids = select(Recommendation.id).where(Recommendation.invoice_id.in_(invoice_ids))
    db.execute(delete(ActionAttempt).where(ActionAttempt.recommendation_id.in_(rec_ids)))
    for model in (Recommendation, PolicyDecisionLog, ReplyTriage, Prediction, Payment):
        db.execute(delete(model).where(model.invoice_id.in_(invoice_ids)))
    db.execute(delete(Invoice).where(Invoice.source == "demo"))
    db.execute(delete(Client).where(Client.source == "demo"))
    db.execute(delete(ReadModel).where(ReadModel.key.like("forecast:demo-%") | ReadModel.key.like("drift:demo-%") | (ReadModel.key == "activity:demo")))
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
