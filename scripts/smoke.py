from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def smoke() -> None:
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/ready")
        assert ready.status_code == 200, ready.text
        payload = client.get("/invoices").json()
        assert payload["synthetic_demo"] is True
        assert payload["count"] >= 1, "Run make seed-demo before the smoke check."
        invoice = client.get(f"/invoices/{payload['items'][0]['id']}")
        assert invoice.status_code == 200, invoice.text
        assert "forecast" in invoice.json()
        assert client.get("/invoices", headers={"X-Smoke-Check": "1"}).status_code == 200


if __name__ == "__main__":
    smoke()
    print("Local API smoke passed: health, readiness, public demo list, detail and forecast.")
