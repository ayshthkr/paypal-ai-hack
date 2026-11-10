# Execution status

## Completed locally

- **00 Bootstrap:** Bun 1.3.6 lockfile, Python `uv` lockfile, Postgres, seven-table Alembic schema, FastAPI/Next app, operator sessions, public synthetic-only reads, `/health` and `/ready`.
- **01 Synthetic journey:** deterministic 25-client/400-invoice data, partial/full/cancelled cases, LightGBM/Kaplan–Meier forecasts, editable approval and simulated payment updates. Browser smoke completed against `demo-invoice-01-12`: edited reminder approved, local action persisted, partial payment reconciled, then invoice reached `PAID` with zero outstanding. No PayPal call was made.
- **02 PayPal adapter implementation:** Sandbox-only OAuth/REST adapter, paginated invoice sync, detail fetch, controlled invoice creation/send, native reminder, allowlisted external payment-recording CLI, bounded safe-read retries, ambiguous mutation handling, webhook verification/storage/deduplication, and authoritative invoice refresh. Sandbox app credentials and the controlled test-buyer allowlist are configured locally and in Render. The `Late Payer Sandbox` app's Invoicing feature is enabled and saved, but fresh OAuth tokens still lack the Invoicing scope; invoice listing/creation return `NOT_AUTHORIZED`. No invoice was created or sent.
- **03 Forecast:** deterministic synthetic history, point-in-time features and censoring, trained LightGBM hazard artifact, Kaplan–Meier fallback, conditional 180-day forecasts, quantiles/probabilities, calibration plot and held-out synthetic report.
- **04 Decisions:** client drift evidence, deterministic wait/friendly/firm policy, cooldown and invoice-state rechecks, exact-text approval hash, idempotent attempts and unknown-delivery handling.
- **05 UI:** AG Grid Community (free MIT edition), invoice sort/filter, currency totals, uncertainty and drift fields, custom SVG survival curve, invoice review, action history, demo payment controls, visible synthetic labels and refresh polling.
- **06 Packaging:** local and Render Blueprint configuration, server-side secrets, health check, migration startup, Bun production build and setup documentation. Render is connected only to `ayshthkr/paypal-ai-hack`; the free Blueprint, Postgres, API and web service are deployed. The API is live at `https://latepayer-api.onrender.com`; `/ready` returns HTTP 200 with Postgres, production operator credentials, Sandbox credentials, the webhook ID, and the model artifact configured. A first-boot-only seeder populated the hosted database with 25 synthetic clients and 400 invoices, preserving existing rows on subsequent starts. The hosted web service at `https://latepayer-web.onrender.com` serves the portfolio through the API proxy; its public synthetic dashboard and forecasts were verified in-browser. `.env.render` is ignored and mode `0600`. Production API refuses readiness and login when operator credentials/session secret are defaults.
- **07 Verification:** final `make check` passed (Ruff, 13 pytest tests, ESLint, TypeScript, production build); final `make smoke` passed; `make train` regenerated the LightGBM artifact/report; local browser demo approval and partial/full payment journey passed. Hosted browser verification showed the synthetic banner, 400 invoice records, forecasts and client drift. The hosted API proxy returned HTTP 200 for readiness, invoice and client requests; hosted operator login and the authenticated session check both returned HTTP 200. `bun install --frozen-lockfile` and `make setup` passed earlier in this run. `.env` contains non-default local operator credentials and Sandbox config, is mode `0600`, and is ignored by Git.

## External work still required

1. PayPal account/KYC is complete per the user's note; no further KYC action is listed. The Sandbox app toggle for Invoicing is on, but PayPal's OAuth token omits `https://api.paypal.com/v1/invoicing:*` and the API returns HTTP 403 `NOT_AUTHORIZED` for invoice listing/creation. A PayPal entitlement/scope fix is needed before an invoice/payment acceptance run. The webhook is registered for invoice paid, cancelled, refunded, and sent events, but delivery has not yet been verified.
2. Render setup is complete: the GitHub App is scoped to the private `ayshthkr/paypal-ai-hack` repository on `codex/late-payer-v1`; the free Blueprint, API, database, operator/Sandbox environment, webhook ID, and web service are configured. The hosted synthetic dashboard and API proxy are verified.
3. PayPal Sandbox invoice import/reminder/payment and actual webhook delivery remain pending until PayPal grants the missing Invoicing scope. No live PayPal operation was attempted.

## Main changed areas

- App and proxy: `app/`, `services/api/app/`
- Schema/model/data: `services/api/alembic/`, `ml/`, `artifacts/`
- Operations: `Makefile`, `scripts/`, `render.yaml`
- Setup/acceptance: `README.md`, `docs/external-setup.md`, `docs/v1-acceptance.md`
- Tests: `tests/`
