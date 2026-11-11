import json
from datetime import date
from pathlib import Path

from ml.synthetic import generate_records


ROOT = Path(__file__).resolve().parents[1]


def test_release_facts_and_walkthrough_follow_the_deployed_artifacts():
    evaluation = json.loads((ROOT / "artifacts/eval.json").read_text())
    metadata = json.loads((ROOT / "artifacts/model_metadata.json").read_text())
    calibration = json.loads((ROOT / "artifacts/calibration.json").read_text())
    fallback = json.loads((ROOT / "public/demo-fallback.json").read_text())
    as_of = date.fromisoformat(evaluation["as_of_date"])
    records = generate_records(observation_cutoff=as_of)
    open_invoices = [
        invoice for invoice in records
        if invoice["status"] not in {"PAID", "CANCELLED", "REFUNDED", "VOIDED", "DRAFT", "DELETED"}
        and invoice["outstanding"] > 0
    ]
    assert fallback["as_of"] == as_of.isoformat()
    assert fallback["invoice_count"] == len(records)
    assert fallback["open_count"] == len(open_invoices)
    assert sum(bucket["count"] for bucket in fallback["aging_buckets"].values()) == fallback["open_count"]

    walkthrough = (ROOT / "walkthrough.md").read_text()
    assert "generated artifacts" in walkthrough
    assert "synthetic metrics are pipeline evidence" in walkthrough.lower()
    assert "five-stop" in walkthrough

    assert evaluation["model_card"]["training_window"] == metadata["training_date_range"]
    assert calibration["model"] == metadata["active_model"]
    assert evaluation["model_card"]["deployed_model"] == metadata["active_model"]
    assert "Cox PH" in evaluation["model_card"]["selection_rule"]
    deployed = evaluation["deployed_forecast_evaluation"]
    assert deployed["model"] == metadata["active_model"]
    assert deployed["brier_by_horizon"]
    assert deployed["limitations"]
