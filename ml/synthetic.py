"""Fixed-seed synthetic receivables with transparent generation rules."""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal


SEED = 20261110
HISTORY_CUTOFF = date(2026, 10, 31)
PROFILES = ("prompt", "slow", "volatile", "drifting", "new")
NAMES = (
    "Northstar Studio", "Juniper Works", "Mosaic Supply", "Blue Cedar Labs", "Beacon Creative",
    "Cinder & Co", "Fieldnote Systems", "Marble House", "Orbit Office", "Pine & Paper",
    "Quiet Harbor", "Redwood Digital", "Silverline Goods", "Tandem Design", "Umber Analytics",
    "Verdant Market", "Westward Media", "Yellow Finch", "Zephyr Workshop", "Amber Current",
    "Brightwater Co", "Copper Finch", "Driftwood Studio", "Evergreen Systems", "Fable Commerce",
)


def _settlement_delay(profile: str, index: int, rng: random.Random) -> int | None:
    if profile == "prompt":
        return max(2, int(rng.gauss(13, 4)))
    if profile == "slow":
        return max(15, int(rng.gauss(53, 13)))
    if profile == "volatile":
        return rng.randint(5, 105)
    if profile == "drifting":
        return max(6, int(rng.gauss(12 + index * 4.2, 9)))
    return max(4, int(rng.gauss(31, 12)))


def generate_records(seed: int = SEED, observation_cutoff: date = HISTORY_CUTOFF) -> list[dict]:
    """Return exactly 25 clients and 400 stable invoice records."""
    rng = random.Random(seed)
    clients = [{"id": f"demo-client-{i + 1:02}", "name": NAMES[i], "profile": PROFILES[i // 5], "source": "demo"} for i in range(25)]
    rows: list[dict] = []
    start = date(2023, 1, 2)
    for c_index, client in enumerate(clients):
        for i in range(16):
            invoice_id = f"demo-invoice-{c_index + 1:02}-{i + 1:02}"
            issue = start + timedelta(days=c_index * 2 + i * 83)
            terms = 14 if c_index % 3 == 0 else (30 if c_index % 3 == 1 else 45)
            due = issue + timedelta(days=terms)
            currency = ("USD", "EUR", "GBP")[c_index % 3]
            amount = Decimal(str(rng.randrange(180, 8200))).quantize(Decimal("0.01"))
            delay = _settlement_delay(client["profile"], i, rng)
            # Sparse cancellation and partial payment fixtures are deterministic and never full events.
            cancelled = (i == 7 and c_index % 8 == 0)
            partial_case = (i == 11 and c_index % 4 == 0)
            outcome_paid_at = issue + timedelta(days=delay) if delay is not None and not cancelled and not partial_case else None
            partial_at = issue + timedelta(days=min(delay or 24, 24)) if partial_case else None
            # Recent invoice cohorts include open right-censored cases for demo dashboards.
            if i == 15 and c_index % 5 in (0, 1, 2):
                outcome_paid_at = None
                partial_at = None
                delay = None
            settled_at = outcome_paid_at if outcome_paid_at and outcome_paid_at <= observation_cutoff else None
            observation_end = min(observation_cutoff, issue + timedelta(days=180))
            observed_settled_at = settled_at if settled_at and settled_at <= observation_end else None
            if cancelled:
                status = "CANCELLED"
                paid = Decimal("0.00")
            elif partial_case and partial_at and partial_at <= observation_cutoff:
                status = "PARTIALLY_PAID"
                paid = (amount * Decimal("0.4")).quantize(Decimal("0.01"))
            elif settled_at and settled_at <= observation_cutoff:
                status = "PAID"
                paid = amount
            elif settled_at and settled_at > observation_cutoff:
                status = "OVERDUE" if observation_cutoff > due else "SENT"
                paid = Decimal("0.00")
            else:
                status = "OVERDUE" if observation_cutoff > due else "SENT"
                paid = Decimal("0.00")
            outstanding = max(Decimal("0.00"), amount - paid)
            rows.append({
                "id": invoice_id, "number": f"LP-{c_index + 1:02}-{i + 1:03}", "client_id": client["id"],
                "client_name": client["name"], "profile": client["profile"], "email": f"billing{c_index + 1:02}@example.test",
                "issue_date": issue, "due_date": due, "terms_days": terms, "total": amount, "paid": paid,
                "outstanding": outstanding, "currency": currency, "status": status, "source": "demo",
                "settled_at": observed_settled_at, "partial_at": partial_at if partial_at and partial_at <= observation_cutoff else None,
                "partial_amount": paid if partial_case else Decimal("0.00"), "cancelled": cancelled,
                "cancelled_at": issue + timedelta(days=2) if cancelled else None,
                "outcome_paid_at": outcome_paid_at, "observation_cutoff": observation_cutoff,
            })
    return rows


def utc_datetime(day: date) -> datetime:
    return datetime.combine(day, time(12, 0), tzinfo=timezone.utc)
