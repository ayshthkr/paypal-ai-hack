"""Warm read-path contract and latency gate, locally or through a hosted proxy."""
from __future__ import annotations
import os
from time import perf_counter
import httpx
from fastapi.testclient import TestClient
from app.main import app


def smoke() -> list[dict]:
    base = os.getenv("SMOKE_BASE_URL")
    client = httpx.Client(base_url=base, timeout=120) if base else TestClient(app)
    timings = []
    with client:
        for path in ("/health", "/ready"):
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code)
        def read(path):
            started = perf_counter()
            response = client.get(path)
            seconds = perf_counter()-started
            assert response.status_code == 200, (path,response.status_code,response.text[:200])
            assert seconds < 2, f"Warm read exceeded 2s: {path}: {seconds:.3f}s"
            timings.append({"path":path,"seconds":round(seconds,3),"bytes":len(response.content),"server_timing":response.headers.get("server-timing")})
            return response.json()
        portfolio = read("/portfolio")
        assert portfolio["count"] >= 1, "Run make seed-demo before smoke."
        row = portfolio["items"][0]
        assert "curve" not in (row["forecast"] or {})
        detail = read(f"/invoices/{row['id']}")
        assert detail.get("forecast") is None or "curve" in detail["forecast"]
        for path in ("/clients",f"/facts/clients/{row['client_id']}","/action-queue","/evaluation","/invoices?view=all"):
            read(path)
        assert client.get("/invoices/does-not-exist").status_code == 404
    return timings


if __name__ == "__main__":
    import json
    print(json.dumps(smoke(),indent=2))
