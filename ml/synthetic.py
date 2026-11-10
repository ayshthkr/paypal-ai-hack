"""Deterministic synthetic receivables with a moving, explicit observation date."""

from __future__ import annotations

import os
import random
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal


SEED = 20261110
PROFILES = ("prompt", "due_date", "chronic_late", "deteriorating", "erratic")
NAMES = (
    "Northstar Studio", "Juniper Works", "Mosaic Supply", "Blue Cedar Labs", "Beacon Creative",
    "Cinder & Co", "Fieldnote Systems", "Marble House", "Orbit Office", "Pine & Paper",
    "Quiet Harbor", "Redwood Digital", "Silverline Goods", "Tandem Design", "Umber Analytics",
    "Verdant Market", "Westward Media", "Yellow Finch", "Zephyr Workshop", "Amber Current",
    "Brightwater Co", "Copper Finch", "Driftwood Studio", "Evergreen Systems", "Fable Commerce",
    "Grove & Grain", "Hearthside Studio", "Indigo Foundry", "Kite & Key", "Lumen Creative",
    "Meadow Analytics", "Nook Commerce", "Oakline Design", "Paper Plane Co", "Quartz Digital",
    "Riverbend Works", "Solstice Supply", "Thistle & Thread", "Union Street Media", "Vista Workshop",
    "Willow Metric", "Xylo Studio", "Yarrow Systems", "Zenith Goods", "Arc & Alder",
    "Brass Lantern", "Cobalt Studio", "Daybreak Labs", "Elm Street Creative", "Foxglove Market",
    "Golden Hour Co", "Harborlight Design",
)

# Four intentionally deteriorating clients make the delay trend visible without
# labelling almost the whole book as drift.
PROFILE_COUNTS = {"prompt": 10, "due_date": 10, "chronic_late": 10, "deteriorating": 4, "erratic": 18}
CHURNED_CLIENT_INDEXES = {2, 16, 43}
OPEN_BUCKET_COUNTS = {"not_due": 20, "late_1_30": 18, "late_31_90": 7, "late_90_plus": 5}
OPEN_COUNT = sum(OPEN_BUCKET_COUNTS.values())
PAYMENT_TERMS = (("DUE_ON_RECEIPT", 0), ("NET_15", 15), ("NET_30", 30))


def resolve_as_of(value: str | date | None = None) -> date:
    """Resolve an explicit ISO date, ``today``, or the current local date."""
    if isinstance(value, date):
        return value
    raw = value or os.getenv("AS_OF", "today")
    if raw.casefold() == "today":
        return date.today()
    return date.fromisoformat(raw)


def _profile_for(index: int) -> str:
    cursor = 0
    for profile, count in PROFILE_COUNTS.items():
        if index < cursor + count:
            return profile
        cursor += count
    raise IndexError(index)


def _cadence_days(index: int) -> int:
    # A deliberate mix: weekly, fortnightly, three-weekly and monthly clients.
    if index % 10 == 0:
        return 7 + (index % 3)
    if index % 5 == 0:
        return 14 + (index % 4)
    if index % 3 == 0:
        return 20 + (index % 6)
    return 28 + (index % 8)


def _open_specs() -> list[tuple[str, int]]:
    """Return (bucket, lateness-days) for the 50 intentionally open invoices."""
    specs = [("not_due", -days) for days in (1, 30, 2, 29, 4, 27, 6, 25, 8, 23,
                                               10, 21, 12, 19, 14, 17, 16, 15, 3, 28)]
    specs += [("late_1_30", days) for days in (1, 30, 3, 28, 5, 26, 7, 24, 9,
                                                22, 11, 20, 13, 18, 15, 16, 2, 29)]
    specs += [("late_31_90", days) for days in (31, 90, 38, 82, 48, 70, 59)]
    # Four ordinary collections cases stay within 120 days of invoice age;
    # the last is the single deliberate older collections case.
    specs += [("late_90_plus", days) for days in (91, 96, 100, 105, 125)]
    return specs


def _settlement_delay(profile: str, index: int, rng: random.Random) -> int:
    if profile == "prompt":
        return rng.randint(-3, 2)
    if profile == "due_date":
        return rng.choice((-1, 0, 0, 0, 1))
    if profile == "chronic_late":
        return 10 + rng.randint(-2, 2)
    if profile == "deteriorating":
        return min(70, max(0, int(index * 1.6 + rng.gauss(1, 3))))
    return rng.randint(-8, 42)


def generate_records(seed: int = SEED, observation_cutoff: date | None = None) -> list[dict]:
    """Generate 52 clients, 1,500–3,000 invoices, and exactly 50 open invoices."""
    cutoff = resolve_as_of(observation_cutoff)
    rng = random.Random(seed)
    clients = [{
        "id": f"demo-client-{i + 1:02}", "name": NAMES[i], "profile": _profile_for(i),
        "source": "demo", "cadence_days": _cadence_days(i),
        "churned": i in CHURNED_CLIENT_INDEXES,
    } for i in range(len(NAMES))]

    specs = _open_specs()
    # 49 active clients receive one open invoice; one additional client gets a
    # second open invoice. The three churned clients have no open balance.
    active_indexes = [i for i in range(len(clients)) if i not in CHURNED_CLIENT_INDEXES]
    open_assignments: list[list[tuple[str, int]]] = [[] for _ in clients]
    spec_indexes = list(range(len(specs)))
    rng.shuffle(spec_indexes)
    for client_index, spec_index in zip(active_indexes, spec_indexes[:len(active_indexes)], strict=True):
        open_assignments[client_index].append(specs[spec_index])
    extra_client = active_indexes[0]
    open_assignments[extra_client].append(specs[spec_indexes[len(active_indexes)]])

    rows: list[dict] = []
    for c_index, client in enumerate(clients):
        profile = client["profile"]
        cadence = client["cadence_days"]
        churned = client["churned"]
        history_start = cutoff - timedelta(days=rng.randint(730, 1095))
        if churned:
            stop_date = cutoff - timedelta(days=rng.randint(90, 180))
            final_issue = min(stop_date, cutoff - timedelta(days=90))
            opens: list[tuple[str, int]] = []
        else:
            opens = open_assignments[c_index]
            # Ensure the older of two open invoices is issued first.
            opens.sort(key=lambda item: item[1], reverse=True)
            final_issue = min(cutoff - timedelta(days=15), min(
                cutoff - timedelta(days=days + rng.choice(PAYMENT_TERMS)[1])
                for _, days in opens
            ))
        open_issue_dates: list[tuple[date, str, int]] = []
        if opens:
            # Choose terms first and derive issue dates directly from due-date
            # buckets, which makes the seeded aging mix exact and repeatable.
            for position, (bucket, lateness) in enumerate(opens):
                if bucket == "not_due":
                    term_type, terms = (("NET_30", 30) if -lateness > 15 else ("NET_15", 15))
                elif bucket == "late_90_plus":
                    term_type, terms = (("DUE_ON_RECEIPT", 0) if lateness > 120 else ("NET_15", 15))
                else:
                    term_type, terms = PAYMENT_TERMS[(c_index + position + 1) % len(PAYMENT_TERMS)]
                issue = cutoff - timedelta(days=lateness + terms)
                open_issue_dates.append((issue, bucket, lateness, term_type, terms))
            open_issue_dates.sort(key=lambda item: item[0])
            final_issue = open_issue_dates[0][0]

        issue_dates: list[tuple[date, str | None, int | None]] = []
        issue = history_start
        while issue < final_issue:
            issue_dates.append((issue, None, None, None, None))
            step = max(7, cadence + rng.randint(-3, 4))
            issue += timedelta(days=step)
        if churned:
            issue_dates.append((final_issue, None, None, None, None))
        else:
            issue_dates.extend(open_issue_dates)
        issue_dates.sort(key=lambda item: item[0])

        for i, (issue, open_bucket, open_lateness, fixed_term_type, fixed_terms) in enumerate(issue_dates, start=1):
            invoice_id = f"demo-invoice-{c_index + 1:02}-{i:03}"
            term_type, terms = (fixed_term_type, fixed_terms) if fixed_term_type else PAYMENT_TERMS[(c_index + i) % len(PAYMENT_TERMS)]
            due = issue + timedelta(days=terms)
            currency = ("USD", "EUR", "GBP")[c_index % 3]
            amount = Decimal(str(rng.randrange(180, 8200))).quantize(Decimal("0.01"))
            is_open = open_bucket is not None
            delay = _settlement_delay(profile, i - 1, rng)
            cancelled = not is_open and not churned and i % 83 == 0 and c_index % 7 == 0
            partial_allowed = rng.random() < 0.36
            partial_case = partial_allowed and (rng.random() < 0.13 or (is_open and i % 4 == 0))
            partial_at = issue + timedelta(days=max(1, min(18, max(1, terms // 2)))) if partial_case else None
            partial_amount = (amount * Decimal(str(rng.choice(("0.25", "0.4", "0.5"))))).quantize(Decimal("0.01")) if partial_case else Decimal("0.00")
            if is_open:
                outcome_paid_at = None
                settled_at = None
                paid = partial_amount if partial_at and partial_at <= cutoff else Decimal("0.00")
                status = "PARTIALLY_PAID" if paid > 0 else ("OVERDUE" if due < cutoff else "SENT")
            elif cancelled:
                outcome_paid_at = None
                settled_at = None
                partial_at = None
                partial_amount = Decimal("0.00")
                paid = Decimal("0.00")
                status = "CANCELLED"
            else:
                # All historical invoices settle by the observation cutoff.
                # This prevents ancient open debt from coexisting with newer
                # paid invoices while retaining the profile's actual lateness.
                pay_date = due + timedelta(days=delay)
                if pay_date < issue:
                    pay_date = issue
                if pay_date > cutoff:
                    pay_date = cutoff
                if partial_case and (pay_date - issue).days >= 2:
                    partial_at = min(partial_at or pay_date, pay_date - timedelta(days=1))
                    partial_amount = min(partial_amount, amount - Decimal("0.01"))
                else:
                    partial_at = None
                    partial_amount = Decimal("0.00")
                outcome_paid_at = pay_date
                settled_at = pay_date
                paid = amount
                status = "PAID"

            viewed_at = issue + timedelta(days=rng.randint(0, min(8, max(0, (cutoff - issue).days)))) if rng.random() < 0.78 and issue <= cutoff else None
            reminder_events = []
            if issue <= cutoff and not cancelled and (is_open or (outcome_paid_at and outcome_paid_at > due)):
                reminder_days = [due + timedelta(days=3)]
                if delay > 14:
                    reminder_days.append(due + timedelta(days=10))
                for reminder_day in reminder_days:
                    sent_at = min(reminder_day, cutoff)
                    if sent_at >= issue and (outcome_paid_at is None or sent_at <= outcome_paid_at):
                        reminder_events.append({
                            "sent_at": datetime.combine(sent_at, time(12), tzinfo=timezone.utc).isoformat(),
                            "tone": "friendly" if len(reminder_events) == 0 else "firm",
                        })

            rows.append({
                "id": invoice_id, "number": f"LP-{c_index + 1:02}-{i:03}",
                "client_id": client["id"], "client_name": client["name"], "profile": profile,
                "client_cadence_days": cadence, "client_churned": churned,
                "email": f"billing{c_index + 1:02}@example.test", "issue_date": issue,
                "due_date": due, "terms_days": terms, "payment_term_type": term_type,
                "total": amount, "paid": paid, "outstanding": max(Decimal("0.00"), amount - paid),
                "currency": currency, "status": status, "source": "demo", "settled_at": settled_at,
                "partial_at": partial_at if partial_at and partial_at <= cutoff else None,
                "partial_amount": partial_amount, "cancelled": cancelled,
                "cancelled_at": issue + timedelta(days=2) if cancelled else None,
                "outcome_paid_at": outcome_paid_at, "observation_cutoff": cutoff,
                "viewed_at": viewed_at, "partial_payment_allowed": partial_allowed,
                "reminder_events": reminder_events, "open_age_bucket": open_bucket,
                "open_lateness_days": open_lateness,
            })
    return rows


def utc_datetime(day: date) -> datetime:
    return datetime.combine(day, time(12, 0), tzinfo=timezone.utc)
