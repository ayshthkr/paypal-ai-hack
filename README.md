# Late Payer

Receivables dashboard with a conditional time-to-payment forecast, client delay signals, and human-approved PayPal invoice reminders. The local experience uses a reproducible synthetic dataset. PayPal calls are restricted to Sandbox and controlled recipient emails.

For the guided screen-by-screen tour, see [walkthrough.md](walkthrough.md).

## Requirements

- Bun 1.3.6
- Python 3.12 or 3.13 with `uv`
- Docker Compose

## Local setup

```sh
test -f .env || cp .env.example .env
# Set OPERATOR_USERNAME, OPERATOR_PASSWORD, and SESSION_SECRET in .env.
bun install
make setup
make dev
```

Open `http://localhost:3000` for the dashboard, `http://localhost:8000/docs` for the API, and `http://localhost:8000/healthz` for the minimal liveness check. `/ready` is not public. Render probes `/health` during deployment; `/healthz` is also available as a minimal liveness endpoint. The dashboard remains usable in public synthetic demo mode without PayPal credentials. `make setup` starts Postgres, migrates the database, seeds 52 deterministic synthetic clients and 1,500–3,000 invoices (with exactly 50 intentionally open), and trains the forecast.

## Commands

- `make seed-demo` — deterministically reset the synthetic fixture set.
- `make train` — train and evaluate LightGBM, Cox PH, and Kaplan–Meier candidates; select by validation integrated Brier score and refit the selected runtime artifact through the release cutoff.
- `make seed-sandbox` — create and send up to three controlled PayPal Sandbox invoices to emails in `PAYPAL_ALLOWED_RECIPIENT_EMAILS`.
- `make sync` — import PayPal invoices and retry verified webhook refreshes.
- `make record-payment INVOICE_ID=... AMOUNT=... CURRENCY=...` — record an already completed external payment against an allowlisted Sandbox invoice, then refresh local state. If PayPal accepted the record but refresh failed, run `make sync` and do not submit the payment again.
- `make check` — Python lint/tests and web lint/typecheck/production build.
- `make smoke` — local API read-path smoke check (requires migrated, seeded database).
- `make clean` — remove local containers, environment and build artifacts.

## PayPal Sandbox configuration

Keep `PAYPAL_MODE=sandbox`. In `.env`, configure the Sandbox REST app's `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`, then set `PAYPAL_ALLOWED_RECIPIENT_EMAILS` to controlled Sandbox buyer accounts. `PAYPAL_SANDBOX_CURRENCY` defaults to USD and can be changed to a currency supported by the Sandbox merchant. Never place secrets in source control. Seed a small set using `make seed-sandbox`, import with `make sync`, and complete checkout as the Sandbox buyer. The app re-fetches invoice state before any reminder.

PayPal's native reminder endpoint controls the email body. The editable text is an internal collection note stored for review; the recipient receives PayPal's system-generated reminder. The UI previews and states this before approval.

After deploying the API, register its HTTPS `/webhooks/paypal` endpoint in the same PayPal app, subscribe to the invoice paid/cancelled/refunded/sent events, and configure `PAYPAL_WEBHOOK_ID`. Webhooks are verified with PayPal, stored, deduplicated, and reconciled from the current invoice resource. Manual sync is available as recovery.

## Operator access and public demo

Configure `OPERATOR_USERNAME`, `OPERATOR_PASSWORD`, and a random `SESSION_SECRET`. Cookies are HttpOnly and same-site; production cookies are secure. Public routes expose only synthetic records. Each browser session gets an isolated copy of the synthetic portfolio, so simulated approvals, replies, and payments do not alter another reviewer's session or the shared base snapshot. Reset rotates only the current browser's demo session. Live PayPal records and provider mutations require operator login; reminder dispatch is sandbox-only and recipient-allowlisted. The static read-only fallback is available at `/offline-demo` when the API is unavailable.

## AG Grid license

The invoice dashboard uses **AG Grid Community**, the free MIT-licensed edition. There is no AG Studio package, paid feature, or license key in this project. The survival curve is a small custom SVG component.

## Gemini API key

`GEMINI_API_KEY` is read only by the API and is never returned by status routes. `/ai/status` reports whether the deployed key is configured and which Gemini model is selected. Reminder drafts and reply classification identify whether Gemini or the deterministic fallback produced the result and show the grounding facts. A template fallback is explicitly labeled.

## Render

`render.yaml` defines the web service, API service, and Postgres database. The API seeds the public synthetic demo on first boot only, preserving its records on later restarts. The web build runs Bun 1.3.6, matching the local lockfile. The private GitHub repository is connected to Render and deployed as a free Blueprint. The API is at `https://latepayer-api.onrender.com`; check its liveness at `/healthz`. Operator and Sandbox secrets stay in Render, while the generated operator login is also saved in ignored local `.env.render` (mode `0600`). The PayPal Sandbox Invoicing scope and invoice operations are verified; the controlled invoice, reminder, payment reconciliation, and webhook flow passed acceptance. Keep `PAYPAL_MODE=sandbox`. Free Render Postgres expires 30 days after creation and has a 14-day grace period before deletion; record the actual dashboard dates and follow [the Render runbook](docs/render-free-tier-runbook.md). Update `WEB_ORIGIN` if you use a custom web domain.

Full external actions and account configuration are listed in [docs/external-setup.md](docs/external-setup.md). Execution evidence and remaining external blockers are in [docs/execution-status.md](docs/execution-status.md) and [docs/v1-acceptance.md](docs/v1-acceptance.md).

## Data and model limits

The synthetic generator uses seed `20261110`, 52 clients, 1,500–3,000 invoices with exactly 50 intentionally open at reset, and five transparent payment-timing profiles. `AS_OF` sets the observation cutoff. Training uses invoice-day risk rows; only full settlement is an event, open invoices are right-censored, and cancelled invoices are excluded. The application produces a 180-day conditional survival curve and p10/p50/p90 date estimates. Release facts are generated in the model artifacts and read by the performance page; they are synthetic pipeline checks, not evidence of real-world forecast accuracy. Expected cash multiplies open balances by each forecast's daily settlement probability and aggregates event mass by calendar week. Gross-balance horizon buckets include disputed or unforecastable exposure so counts and amounts reconcile to the open book. Client drift is a descriptive heuristic, not a churn probability; only newly worsening behavior or a strong overdue outlier is counted as an action signal.

- The reminder-uplift table is an assumed sensitivity analysis; synthetic outcomes do not estimate reminder causality.
- The validation-calibrated coverage interval uses paid invoices; right-censoring and client dependence limit coverage guarantees.
- Drift-backtest labels are defined by the synthetic generator and measure recovery of those rules, not real-world risk.
- The separate public benchmark is a historical, non-commercial external pipeline check and does not establish production accuracy.

The reproducible [public dataset evaluation report](docs/public-dataset-evaluation.md) includes its source, license, sample, and external results.
