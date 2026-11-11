# Late Payer — repo handoff

Read this first when continuing work in another chat. Check `git status` before editing and preserve the user's uncommitted `AGENTS.md` changes.

## What this repo is

Late Payer is a receivables dashboard with payment-date forecasts, descriptive client-delay signals, and operator-approved invoice reminders. Stack: Next.js 16 / React / TypeScript with Bun 1.3.6, FastAPI / Python / `uv`, and Postgres. AG Grid Community is used under its free MIT license. The public dashboard is synthetic-only; provider operations are PayPal Sandbox-only and recipient-allowlisted.

- Hosted demo: <https://latepayer-web.onrender.com>
- API health: <https://latepayer-api.onrender.com/health>
- Local UI/API: <http://localhost:3000> and <http://localhost:8000/docs>

## Current state

### Active application release — commit `eb0b407`, 2026-11-12

- Render API and web run `eb0b407`. `/health` and `/healthz` return 200; `/ready` is not implemented. Startup memory pressure from the Nov 11 incident was addressed in `09e7195`.
- The release implements the reviewer workflow fixes across forecasting, collections, replies, actions, scenarios, performance, operator controls, and the packaged fallback. Follow-up fixes reconcile weekly cash cards to invoice probability-mass contributors, align the first-visit queue count with its ranked actions, label filtered versus portfolio-wide summaries, and keep survival-chart dates and table readable.
- Collections and reply flows retain edits through errors, distinguish save from refresh failures, and provide status reconciliation. Synthetic payment retries reuse stable identities. The packaged fallback is directly reachable from failure states.
- `make check` passes: Ruff, 96 Python tests, ESLint, TypeScript, and the Next.js production build. Seven hosted Playwright checks cover cash-card/API reconciliation, queue-count reconciliation, empty-search scope, controlled retry, reply timeout recovery, the five-stop tour/fallback, and collections save/reload.
- Hosted routes `/`, `/actions`, `/collections`, `/tour`, `/offline-demo`, `/performance`, `/operator`, and `/login`, plus API `/health`, `/healthz`, `/action-queue`, `/demo-stories`, and `/evaluation`, returned HTTP 200. Portfolio and action-queue proxy reads took 19.95s and 14.10s in the recorded run. The retry test injects one 503 and verifies recovery; it does not force Render to sleep.
- The 34 ordered hosted screenshots are in `artifacts/screenshots/review-2026-11-12/`. PayPal remained in Sandbox, and this implementation/capture pass did not send a reminder or alter a PayPal record.
- The health workflow is enabled and scheduled every five minutes, but recorded scheduled history was sparse. Render Free can sleep; scheduled jobs are best-effort.

### Earlier deployed state and evidence

- Original V1 acceptance covered a 25-client / 400-invoice fixture and a LightGBM survival forecast. Those model metrics and historical test counts are superseded by the active release-candidate facts in `docs/v1-acceptance.md`.
- PayPal Sandbox OAuth/Invoicing access works. Controlled invoice, reminder, payment reconciliation, and webhook delivery were verified. The webhook invoice ID is nested at `resource.invoice.id`; the adapter handles this. Other recent fixes reconcile create responses without IDs and normalize `MARKED_AS_PAID` to `PAID`.
- Hosting behavior: free Render instances can sleep when idle. The dashboard retries up to 30 times at 2.5-second intervals and aborts sibling reads on failure; portfolio invoice/client reads share batched request context and reuse the client activity fit. The scheduled health workflow runs every five minutes to reduce cold starts, but scheduled jobs remain best-effort.
- Current hosted data is larger than the original acceptance fixture (2,147 invoices / 52 clients observed); the original 400-invoice synthetic acceptance remains the V1 baseline.
- The active reminder workflow calls Gemini for grounded draft generation and reply classification. The key is managed in Render and ignored local environment files; never print, log, or commit it.
- Live PayPal credentials/payments are not configured. No KYC, PayPal support, or paid AG Grid action is pending for Sandbox V1.

Application release `eb0b407` is pushed to `origin/codex/late-payer-v1`; both Render services are deployed. Preserve the user's uncommitted `docs/final-walkthrough-audit.md` and any existing `AGENTS.md` or `walkthrough.md` changes.

## Next checks

1. The manual full-read workflow `43090308092` passed after deployment `da6e91b`. Verify that the re-anchored cron creates a scheduled run; the previous history did not establish a reliable five-minute cadence.
2. Keep PayPal in Sandbox mode and preserve the recipient allowlist for any future provider walkthrough.
3. Preserve `AGENTS.md` changes when making later edits.

## Run it

Requirements: Bun 1.3.6, Python 3.12/3.13 with `uv`, and Docker Compose.

```sh
test -f .env || cp .env.example .env
# Set non-default OPERATOR_USERNAME, OPERATOR_PASSWORD, SESSION_SECRET.
make setup
make dev
```

`make setup` installs dependencies, starts Postgres, migrates the schema, seeds the synthetic fixtures, and trains the model. `make dev` starts UI and API. Useful commands: `make seed-demo` resets synthetic fixtures; `make train` retrains/evaluates; `make sync` imports PayPal state and retries stored webhook refreshes; `make check` runs lint/tests/typecheck/build; `make smoke` checks the local read path.

`make seed-sandbox` creates and sends up to three Sandbox invoices to allowlisted buyers. `make record-payment ...` records an externally completed payment through PayPal Sandbox; it does not charge a buyer. For uncertain provider outcomes, sync and inspect before retrying to avoid duplicates.

## How the app fits together

Browser → Next.js pages and `/api/...` proxy → FastAPI → Postgres. FastAPI owns authentication, reminder rules, forecasts, PayPal calls, and invoice reconciliation. PayPal webhooks are signature-verified, stored/deduplicated, then reconciled from the current provider invoice.

- `app/page.tsx`, `app/invoices/` — dashboard, invoice detail, forecast chart, demo actions.
- `app/api/[...path]/route.ts` — proxy to the API.
- `services/api/app/` — API, models, auth, PayPal adapter, sync, forecast, drift, and policy.
- `ml/`, `artifacts/` — synthetic history, features, model and evaluation outputs.
- `scripts/`, `Makefile` — setup, training, sync, seed and verification commands.
- `walkthrough.md` — guided UI / Sandbox tour; `docs/v1-acceptance.md` — acceptance evidence.

Forecasts estimate time to full payment over 180 days, conditional on the balance still being unpaid today. Client drift is a heuristic, not churn probability. Synthetic demo actions never call PayPal. PayPal’s native reminder controls the actual email body; the approved editable copy is retained for audit.

## Instructions for future chats

Follow the repo’s `AGENTS.md`. In particular, **before writing Next.js code**, read the relevant guide under `node_modules/next/dist/docs/` and heed its version-specific instructions. Keep secrets in ignored `.env` / hosting settings; never add credentials to docs, logs, commits, or chat. Check and preserve the existing working tree before making changes.
