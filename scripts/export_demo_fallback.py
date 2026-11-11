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

    def invoice_context(row: dict) -> dict:
        return {
            "invoice_number": row["number"], "client_name": row["client_name"],
            "currency": row["currency"], "outstanding": format(Decimal(row["outstanding"]), ".2f"),
            "due_date": row["due_date"].isoformat(), "status": row["status"],
            "disputed": bool(row["disputed"]),
            "overdue_days": max(0, (today - row["due_date"]).days),
        }

    overdue_row = min(
        (row for row in open_rows if not row["disputed"] and row["due_date"] < today),
        key=lambda row: row["due_date"], default=None,
    )
    verdant_rows = sorted(
        (row for row in open_rows if row["client_name"] == "Verdant Market"),
        key=lambda row: (row["due_date"], row["number"]),
    )
    action_row = max(
        (row for row in open_rows if not row["disputed"] and row["due_date"] < today and row["reminder_events"]),
        key=lambda row: (len(row["reminder_events"]), row["due_date"]), default=None,
    )
    dispute_row = next((row for row in open_rows if row["disputed"]), None)
    evaluation_path = Path("artifacts/eval.json")
    metadata_path = Path("artifacts/model_metadata.json")
    evaluation = json.loads(evaluation_path.read_text()) if evaluation_path.exists() else {}
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    backtest = evaluation.get("backtest", {})
    workflow_examples = {
        "portfolio": {
            "title": "Portfolio walkthrough example",
            "body": "Trace one open balance from the portfolio totals to its invoice due date and current status.",
            "invoice": invoice_context(overdue_row) if overdue_row else None,
            "facts": [f"Snapshot date: {today.isoformat()}", "Totals are grouped by currency."],
        },
        "verdant_outlier": {
            "title": "Verdant Market · overdue outlier",
            "body": "This unpaid invoice is at least this many days overdue. Its age is a lower bound, not a forecast of when it will be paid.",
            "invoice": invoice_context(verdant_rows[0]) if verdant_rows else None,
            "facts": ["No settled payment date exists for this open balance."],
        },
        "action_queue": {
            "title": "Action queue · approval example",
            "body": "Check the balance and recorded reminder history, then compare the live queue recommendation and delivery preview before an operator approves. This offline example performs no action and sends nothing.",
            "invoice": invoice_context(action_row) if action_row else None,
            "facts": ([
                f"Recorded synthetic reminders: {len(action_row['reminder_events'])}",
                "Existing reminders are history; they do not imply a causal payment effect.",
            ] if action_row else []),
        },
        "model_performance": {
            "title": "Model performance · metric example",
            "body": "Use the generated held-out evaluation to explain how the release model is measured and where interval coverage excludes unbounded cases.",
            "facts": [
                f"Model: {metadata.get('model_version', evaluation.get('model_card', {}).get('deployed_model', 'unavailable'))}",
                f"Paid test invoices: {backtest.get('paid_invoice_count', 0)}",
                f"Eligible interval coverage: {backtest.get('coverage_hit_count', '—')}/{backtest.get('coverage_invoice_count', 0)} ({(backtest.get('p10_p90_coverage') or 0) * 100:.1f}%)",
                f"Paid invoices without a finite interval: {max(0, backtest.get('paid_invoice_count', 0) - backtest.get('coverage_invoice_count', 0))}",
            ],
        },
        "reply_triage": {
            "title": "Reply triage · review example",
            "body": "Inspect the recorded dispute state, then show the preview, correction, and approval sequence using the illustrative message below. It is not a captured customer message; this offline stop does not create or approve a triage record.",
            "invoice": invoice_context(dispute_row) if dispute_row else None,
            "facts": ['Illustrative reply: “We dispute this invoice; please pause reminders while we review it.”', "Only an approved triage action changes invoice state in the live workflow."],
        },
    }
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
        "workflow_examples": workflow_examples,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


if __name__ == "__main__":
    result = export()
    print(json.dumps({key: result[key] for key in ("as_of", "invoice_count", "open_count", "overdue_count", "disputed_count", "currency_totals")}, indent=2))
