"""Counterfactual reminder replay; assumptions, never observed causal effects."""
import random
from datetime import timedelta
from types import SimpleNamespace


def replay(subjects, seed=20261110):
    from app.services.policy import decide
    rng = random.Random(seed)
    base = treated = exposure = 0.0
    eligible = reminded = 0
    for row, duration, event in subjects:
        if not event:
            continue
        amount = float(row['total'])
        exposure += amount
        base += amount * duration
        saved = 0
        review_date = row['due_date'] + timedelta(days=3)
        if review_date < row['outcome_paid_at']:
            invoice = SimpleNamespace(outstanding=amount, currency=row['currency'], disputed=False,
                status='OVERDUE', due_date=row['due_date'], reminder_step=0, reminder_events=[],
                partial_payment_allowed=row.get('partial_payment_allowed', False))
            decision = decide(invoice, .1, today=review_date, probability_14d=.4)
            if decision['action'] in {'friendly','firm'}:
                eligible += 1
                if rng.random() < .1:
                    saved = min(5, (row['outcome_paid_at']-review_date).days)
                    reminded += 1
        treated += amount * (duration-saved)
    return {'label':'Simulation · assumed effects, not observed uplift', 'invoice_count':sum(event for _,_,event in subjects),
            'eligible_invoice_count':eligible,'simulated_accelerated_count':reminded,
            'no_reminder_weighted_dso_days':round(base/exposure,2) if exposure else None,
            'policy_weighted_dso_days':round(treated/exposure,2) if exposure else None,
            'simulated_dso_saved_days':round((base-treated)/exposure,2) if exposure else None,
            'assumption':'At due+3, eligible friendly/firm reminders have a 10% chance of advancing settlement by up to five days. No causal effect is estimated from synthetic outcomes.'}
