import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from ml.drift import client_drift
from ml.synthetic import generate_records


ROOT = Path(__file__).resolve().parents[1]


def test_walkthrough_demo_numbers_match_seed_and_evaluation_artifact():
    evaluation = json.loads((ROOT / "artifacts/eval.json").read_text())
    metadata = json.loads((ROOT / "artifacts/model_metadata.json").read_text())
    calibration = json.loads((ROOT / "artifacts/calibration.json").read_text())
    as_of = date.fromisoformat(evaluation["as_of_date"])
    records = generate_records(observation_cutoff=as_of)
    by_client = defaultdict(list)
    for invoice in records:
        by_client[invoice["client_id"]].append(invoice)

    open_invoices = [
        invoice for invoice in records
        if invoice["status"] not in {"PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}
        and invoice["outstanding"] > 0
    ]
    overdue_invoices = [invoice for invoice in open_invoices if invoice["due_date"] < as_of]
    signals = sum(
        client_drift(invoices, as_of)["signal_type"]
        in {"paying_later", "later_than_usual", "gone_quiet", "habitually_late"}
        for invoices in by_client.values()
    )
    walkthrough = (ROOT / "walkthrough.md").read_text()
    baseline = (
        f"**{len(by_client)} synthetic clients, {len(records):,} invoices, "
        f"{len(open_invoices)} open balances, {len(overdue_invoices)} past-due invoices, "
        f"and {signals} client timing signals**"
    )
    assert baseline in walkthrough

    backtest = evaluation["backtest"]
    assert f"**{backtest['paid_invoice_count']} paid invoices**" in walkthrough
    assert f"**{backtest['p50_median_absolute_error_days']:g}-day median absolute P50 date error**" in walkthrough
    coverage_percent = backtest["p10_p90_coverage"] * 100
    target_percent = backtest["target_coverage"] * 100
    assert f"**{coverage_percent:.1f}%** against its **{target_percent:.0f}% target**" in walkthrough
    assert evaluation["model_card"]["training_window"] == metadata["training_date_range"]
    assert calibration["model"] == metadata["active_model"]
