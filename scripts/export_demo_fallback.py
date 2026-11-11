"""Refresh the read-only portfolio snapshot used when the API cannot be reached."""
from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from ml.synthetic import generate_records, resolve_as_of


def export(path: Path = Path("public/demo-fallback.json")) -> dict:
    today = resolve_as_of()
    records = generate_records(observation_cutoff=today)
    open_rows = [
        row for row in records
        if row["status"] not in {"CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED", "PAID"}
        and Decimal(row["outstanding"]) > 0
    ]
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for row in open_rows:
        totals[row["currency"]] += Decimal(row["outstanding"])
    buckets = {
        "not_due": {"count": 0, "amounts": defaultdict(Decimal)},
        "late_1_30": {"count": 0, "amounts": defaultdict(Decimal)},
        "late_31_60": {"count": 0, "amounts": defaultdict(Decimal)},
        "late_61_89": {"count": 0, "amounts": defaultdict(Decimal)},
        "late_90_plus": {"count": 0, "amounts": defaultdict(Decimal)},
    }
    for row in open_rows:
        late = (today - row["due_date"]).days
        key = "not_due" if late <= 0 else "late_1_30" if late <= 30 else "late_31_60" if late <= 60 else "late_61_89" if late < 90 else "late_90_plus"
        buckets[key]["count"] += 1
        buckets[key]["amounts"][row["currency"]] += Decimal(row["outstanding"])
    samples = sorted(open_rows, key=lambda row: (-int(row["disputed"]), row["due_date"], row["number"]))[:12]
    payload = {
        "as_of": today.isoformat(),
        "generated_from": "ml.synthetic.generate_records · seed 20261110",
        "invoice_count": len(records),
        "open_count": len(open_rows),
        "overdue_count": sum(row["due_date"] < today for row in open_rows),
        "disputed_count": sum(bool(row["disputed"]) for row in open_rows),
        "currency_totals": {key: format(value.quantize(Decimal("0.01")), ".2f") for key, value in sorted(totals.items())},
        "aging_buckets": {
            key: {"count": value["count"], "amounts": {currency: format(amount.quantize(Decimal("0.01")), ".2f") for currency, amount in sorted(value["amounts"].items())}}
            for key, value in buckets.items()
        },
        "sample": [{
            "id": row["id"], "invoice_number": row["number"], "client_name": row["client_name"],
            "currency": row["currency"], "outstanding": format(Decimal(row["outstanding"]), ".2f"),
            "due_date": row["due_date"].isoformat(), "status": row["status"], "disputed": bool(row["disputed"]),
        } for row in samples],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


if __name__ == "__main__":
    result = export()
    print(json.dumps({key: result[key] for key in ("as_of", "invoice_count", "open_count", "overdue_count", "disputed_count", "currency_totals")}, indent=2))
