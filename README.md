# Late Payer

Receivables dashboard with a conditional time-to-payment forecast, client delay signals, and human-approved PayPal invoice reminders. The local experience uses a reproducible synthetic dataset. PayPal calls are restricted to Sandbox and controlled recipient emails.

## Requirements

- Bun 1.3.6
- Python 3.12 or 3.13 with `uv`
- Docker Compose

## Local setup

```sh
cp .env.example .env
# Set OPERATOR_USERNAME, OPERATOR_PASSWORD, and SESSION_SECRET in .env.
bun install
make setup
make dev
```

Open `http://localhost:3000` for the dashboard, `http://localhost:8000/docs` for the API, and `http://localhost:8000/ready` for readiness. The dashboard remains usable in public synthetic demo mode without PayPal credentials. `make setup` starts Postgres, migrates the database, seeds 25 deterministic synthetic clients and 400 invoices, and trains the forecast.

## Commands

- `make seed-demo` — deterministically reset the synthetic fixture set.
- `make train` — train and evaluate the daily LightGBM hazard model; the application uses Kaplan–Meier when validation selects it or the model/history is unavailable.
- `make seed-sandbox` — create and send up to three controlled PayPal Sandbox invoices to emails in `PAYPAL_ALLOWED_RECIPIENT_EMAILS`.
- `make sync` — import PayPal invoices and retry verified webhook refreshes.
- `make record-payment INVOICE_ID=... AMOUNT=... CURRENCY=...` — record an already completed external payment against an allowlisted Sandbox invoice, then refresh local state. If PayPal accepted the record but refresh failed, run `make sync` and do not submit the payment again.
- `make check` — Python lint/tests and web lint/typecheck/production build.
- `make smoke` — local API read-path smoke check (requires migrated, seeded database).
- `make clean` — remove local containers, environment and build artifacts.

## PayPal Sandbox configuration

Keep `PAYPAL_MODE=sandbox`. In `.env`, configure the Sandbox REST app's `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`, then set `PAYPAL_ALLOWED_RECIPIENT_EMAILS` to controlled Sandbox buyer accounts. `PAYPAL_SANDBOX_CURRENCY` defaults to USD and can be changed to a currency supported by the Sandbox merchant. Never place secrets in source control. Seed a small set using `make seed-sandbox`, import with `make sync`, and complete checkout as the Sandbox buyer. The app re-fetches invoice state before any reminder.

PayPal's native reminder endpoint controls the email body. The operator can edit and approve the displayed copy, which is stored for audit, while the actual request asks PayPal to send its own reminder template. The UI states this before approval.

After deploying the API, register its HTTPS `/webhooks/paypal` endpoint in the same PayPal app, subscribe to the invoice paid/cancelled/refunded/sent events, and configure `PAYPAL_WEBHOOK_ID`. Webhooks are verified with PayPal, stored, deduplicated, and reconciled from the current invoice resource. Manual sync is available as recovery.

## Operator access and public demo

Configure `OPERATOR_USERNAME`, `OPERATOR_PASSWORD`, and a random `SESSION_SECRET`. Cookies are HttpOnly and same-site; production cookies are secure. Public routes expose only synthetic records. Demo approval and payment buttons change synthetic data and never call PayPal. Live PayPal records and provider mutations require operator login; reminder dispatch is sandbox-only and recipient-allowlisted.

## AG Grid license

The invoice dashboard uses **AG Grid Community**, the free MIT-licensed edition. There is no AG Studio package, paid feature, or license key in this project. The survival curve is a small custom SVG component.

## Render

`render.yaml` defines the web service, API service, and Postgres database. Deploy it as a Blueprint, fill the prompted secrets in Render, keep `PAYPAL_MODE=sandbox`, and check `/ready`. The blueprint uses free instance plans for the web/API and database; select a persistent database plan in Render if its current free-tier retention is insufficient for the data you intend to keep. Update `WEB_ORIGIN` if you use a custom web domain. The current Render dashboard session and deployment are not connected to this workspace, so no hosted deployment or webhook delivery is claimed.

Full external actions and account configuration are listed in [docs/external-setup.md](docs/external-setup.md). Execution evidence and remaining external blockers are in [docs/execution-status.md](docs/execution-status.md) and [docs/v1-acceptance.md](docs/v1-acceptance.md).

## Data and model limits

The synthetic generator uses seed `20261110`, 25 clients, 400 invoices, five transparent payment-timing profiles, and a cutoff of 2026-10-31. Training uses invoice-day risk rows; only full settlement is an event, open invoices are right-censored, and cancelled invoices are excluded. The application produces a 180-day conditional survival curve and p10/p50/p90 date estimates. Metrics and calibration report are synthetic pipeline checks, not evidence of real-world forecast accuracy. Client drift is a descriptive heuristic, not a churn probability.
