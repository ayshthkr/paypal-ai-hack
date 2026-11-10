from __future__ import annotations

import json

from app.db import SessionLocal
from app.services.paypal import PayPalAdapter
from app.services.sync import sync_paypal


if __name__ == "__main__":
    db = SessionLocal()
    try:
        print(json.dumps(sync_paypal(db, PayPalAdapter()), indent=2))
    finally:
        db.close()
