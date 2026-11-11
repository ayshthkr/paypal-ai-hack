# Late Payer — repo handoff

Read this first when continuing work in another chat. Check `git status` before editing and preserve the user's uncommitted `AGENTS.md` changes.

## What this repo is

Late Payer is a receivables dashboard with payment-date forecasts, descriptive client-delay signals, and operator-approved invoice reminders. Stack: Next.js 16 / React / TypeScript with Bun 1.3.6, FastAPI / Python / `uv`, and Postgres. AG Grid Community is used under its free MIT license. The public dashboard is synthetic-only; provider operations are PayPal Sandbox-only and recipient-allowlisted.

- Hosted demo: <https://latepayer-web.onrender.com>
- API health: <https://latepayer-api.onrender.com/health>
- Local UI/API: <http://localhost:3000> and <http://localhost:8000/docs>

## Current state

### Active application release — commit `09e7195`, 2026-11-12

- Render API and web are live with application code from `09e7195`. On Nov 11 the API repeatedly exceeded Render Free's 512 MB memory limit and stopped. The database remained available. Startup logs showed the demo seeder rebuilding the synthetic dataset when the merchant date advanced, loading a large cached snapshot and stale forecast rows into Python, and `uv run --frozen` syncing development tools at runtime. The replacement boot path advances the demo clock without rebuilding invoices, reads only the snapshot version, invalidates stale forecast rows with set-based SQL, and uses `uv run --no-sync`. The `/health` deployment probe and `/healthz` liveness route now return 200; `/ready` is not implemented.
- Browser verification after `09e7195`: the portfolio rendered, `/actions` displayed the simulated queue, Ask answered “Which overdue invoice has the largest open balance?” with a grounded response, and `/performance` rendered its held-out synthetic report. The action queue API returned 200 in about 4.5 seconds. A cold portfolio read took about 11.3 seconds; the next warm read took about 5.4 seconds. Direct `/healthz` returned 200 in about 1 second. The deployment logs show no recurrence of the 512 MB startup failure.
- `.github/workflows/demo-health.yml` now pings API `/healthz` and web `/` every five minutes, with retries, to reduce free-instance sleep. The workflow is active on this repository's default branch. Manual runs verify the full read path. This reduces the known startup risk but cannot promise uninterrupted availability: Render Free can still sleep, and GitHub scheduled jobs are best-effort.
- Local verification passes: Ruff, 91 Python tests, ESLint, TypeScript, and the Next.js production build. A browser-to-PayPal Sandbox invoice creation test also passed with the controlled recipient allowlist and verified invoice reconciliation.
- Invoice creation now forwards the browser idempotency key through the web proxy. Forecast summaries retain weekly settlement probabilities and old summaries are invalidated. Evaluation now replays the deployed scoring path, includes known positive outcomes before each horizon in IPCW Brier scoring, and reports interval eligibility and exclusions consistently.
- Approval recovery reconciles server state after a failed or timed-out response. Synthetic queue actions say they are simulated; the terms simulator describes associations and uncertainty; the offline snapshot includes illustrative workflow examples; the tour retains navigation controls across destination screens.
- Runtime model selection now compares LightGBM, Kaplan–Meier, and Cox PH on validation IPCW integrated Brier; the checked-in release artifact selects Cox PH (`cox-ph-v7-seed-20261110-asof-2026-11-11`). `artifacts/eval.json` separately reports the complete deployed scoring-path replay and synthetic-data limitations.
- Cash summaries use probability-weighted settlement mass consistently, and disputed/unforecastable open balances reconcile into a separate gross exposure bucket. Hypothetical terms scoring now uses new issue/due dates.
- Public synthetic mutations are isolated per browser session, and `/offline-demo` serves a generated, read-only snapshot. Dashboard clock changes are committed with their data; action and cash exposure are above the fold.
- Reply triage has a persisted preview/correct/approve/dismiss flow and dispute resolution. Collections review stores owner, notes, follow-up date, and resolution. PayPal reminder screens disclose that the provider sends its native email; edits remain internal notes.
- Alerts exclude habitual lateness and require stronger recent-worsening/outlier evidence. The generated synthetic backtest has 9 flags and 5 generator-defined positives; it does not establish real-world precision.
- `/ai/status` reports Google Gemini `gemini-3.5-flash-lite`, `configured: true`. A live isolated synthetic run returned a Gemini draft grounded in invoice LP-48-021 (GBP 6,967.00, due 2026-10-31), persisted the exact model provenance, and returned a Gemini-classified promise-to-pay reply preview dated 2026-11-20 in pending state. The hosted invoice UI shows the provider, model, and grounding label. No reminder was sent and no PayPal record was changed.
- The scheduled `.github/workflows/demo-health.yml` checks core web/API liveness every five minutes. Manual full-read checks include portfolio, invoice detail, action queue, morning brief, demo stories, evaluation, and AI status. Free Render instances may still sleep between scheduled runs, so the schedule reduces cold starts without promising uninterrupted warm capacity.

### Earlier deployed state and evidence

- Original V1 acceptance covered a 25-client / 400-invoice fixture and a LightGBM survival forecast. Those model metrics and historical test counts are superseded by the active release-candidate facts in `docs/v1-acceptance.md`.
- PayPal Sandbox OAuth/Invoicing access works. Controlled invoice, reminder, payment reconciliation, and webhook delivery were verified. The webhook invoice ID is nested at `resource.invoice.id`; the adapter handles this. Other recent fixes reconcile create responses without IDs and normalize `MARKED_AS_PAID` to `PAID`.
- Hosting behavior: free Render instances can sleep when idle. The dashboard retries up to 30 times at 2.5-second intervals and aborts sibling reads on failure; portfolio invoice/client reads share batched request context and reuse the client activity fit. The scheduled health workflow runs every 10 minutes to reduce cold starts.
- Current hosted data is larger than the original acceptance fixture (2,147 invoices / 52 clients observed); the original 400-invoice synthetic acceptance remains the V1 baseline.
- The active reminder workflow calls Gemini for grounded draft generation and reply classification. The key is managed in Render and ignored local environment files; never print, log, or commit it.
- Live PayPal credentials/payments are not configured. No KYC, PayPal support, or paid AG Grid action is pending for Sandbox V1.

Application release `09e7195` (`fix: keep demo snapshot checks lightweight`) is pushed to `origin/codex/late-payer-v1`; both Render services are deployed. Preserve the user's modified `AGENTS.md` and `walkthrough.md`.

## Next checks

1. After a deployment, run `gh workflow run demo-health.yml` and confirm the manual read-path workflow succeeds. The scheduled liveness pings are best-effort on GitHub and Render Free.
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
