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
    assert fallback["snapshot_version"] == "late-payer-fallback-v2"
    assert fallback["invoice_count"] == len(records)
    assert fallback["open_count"] == len(open_invoices)
    assert sum(bucket["count"] for bucket in fallback["aging_buckets"].values()) == fallback["open_count"]
    verdant = fallback["workflow_examples"]["verdant_outlier"]["invoice"]
    assert (verdant["invoice_number"], verdant["client_name"], verdant["due_date"]) == (
        "LP-16-045", "Verdant Market", "2026-07-09",
    )
    assert {"issue_date", "total", "paid", "outstanding", "payment_term_type", "reminder_dates"} <= verdant.keys()
    assert fallback["workflow_examples"]["reply_triage"]["invoice"]["invoice_number"] == "LP-06-051"

    walkthrough = (ROOT / "walkthrough.md").read_text()
    assert "release artifact" in walkthrough.lower()
    assert "synthetic results are pipeline evidence" in walkthrough.lower()
    assert "five-stop" in walkthrough.lower()

    assert evaluation["model_card"]["training_window"] == metadata["training_date_range"]
    assert calibration["model"] == metadata["active_model"]
    assert evaluation["model_card"]["deployed_model"] == metadata["active_model"]
    assert "Cox PH" in evaluation["model_card"]["selection_rule"]
    deployed = evaluation["deployed_forecast_evaluation"]
    assert deployed["model"] == metadata["active_model"]
    assert deployed["brier_by_horizon"]
    assert deployed["limitations"]
