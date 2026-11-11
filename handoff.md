# Late Payer — repo handoff

Read this first when continuing work in another chat. Check `git status` before editing and preserve the user's uncommitted `AGENTS.md` changes.

## What this repo is

Late Payer is a receivables dashboard with payment-date forecasts, descriptive client-delay signals, and operator-approved invoice reminders. Stack: Next.js 16 / React / TypeScript with Bun 1.3.6, FastAPI / Python / `uv`, and Postgres. AG Grid Community is used under its free MIT license. The public dashboard is synthetic-only; provider operations are PayPal Sandbox-only and recipient-allowlisted.

- Hosted demo: <https://latepayer-web.onrender.com>
- API/readiness: <https://latepayer-api.onrender.com/ready>
- Local UI/API: <http://localhost:3000> and <http://localhost:8000/docs>

## Current state

### Active release — commit `8bf6675`, 2026-11-11

- Render reports API and web deployed at `8bf6675`. Live portfolio, queue, brief, stories, evaluation, AI status, web proxy, dashboard, performance page, action queue, tour stop persistence, and `/offline-demo?stop=2` were verified. The portfolio reports Cox and 50 open balances; horizon counts reconcile, including disputed GBP 7,539.
- Local verification passes: Ruff, 86 Python tests, ESLint, TypeScript, and the Next.js production build.
- Runtime model selection now compares LightGBM, Kaplan–Meier, and Cox PH on validation IPCW integrated Brier; the checked-in release artifact selects Cox PH (`cox-ph-v7-seed-20261110-asof-2026-11-11`). `artifacts/eval.json` separately reports the complete deployed scoring-path replay and synthetic-data limitations.
- Cash summaries use probability-weighted settlement mass consistently, and disputed/unforecastable open balances reconcile into a separate gross exposure bucket. Hypothetical terms scoring now uses new issue/due dates.
- Public synthetic mutations are isolated per browser session, and `/offline-demo` serves a generated, read-only snapshot. Dashboard clock changes are committed with their data; action and cash exposure are above the fold.
- Reply triage has a persisted preview/correct/approve/dismiss flow and dispute resolution. Collections review stores owner, notes, follow-up date, and resolution. PayPal reminder screens disclose that the provider sends its native email; edits remain internal notes.
- Alerts exclude habitual lateness and require stronger recent-worsening/outlier evidence. The generated synthetic backtest has 9 flags and 5 generator-defined positives; it does not establish real-world precision.
- `/ai/status` reports Google Gemini `gemini-3.5-flash-lite`, `configured: true`. A live isolated synthetic run returned a Gemini draft grounded in invoice LP-48-021 (GBP 6,967.00, due 2026-10-31), persisted the exact model provenance, and returned a Gemini-classified promise-to-pay reply preview dated 2026-11-20 in pending state. The hosted invoice UI shows the provider, model, and grounding label. No reminder was sent and no PayPal record was changed.
- The scheduled `.github/workflows/demo-health.yml` checks core web/API reads every 10 minutes, including AI configuration. Manual run `43056612066` succeeded on `8bf6675` in 8 seconds. Free Render instances may still sleep between runs, so the schedule reduces cold starts without promising uninterrupted warm capacity.

### Earlier deployed state and evidence

- Original V1 acceptance covered a 25-client / 400-invoice fixture and a LightGBM survival forecast. Those model metrics and historical test counts are superseded by the active release-candidate facts in `docs/v1-acceptance.md`.
- PayPal Sandbox OAuth/Invoicing access works. Controlled invoice, reminder, payment reconciliation, and webhook delivery were verified. The webhook invoice ID is nested at `resource.invoice.id`; the adapter handles this. Other recent fixes reconcile create responses without IDs and normalize `MARKED_AS_PAID` to `PAID`.
- Hosting behavior: free Render instances can sleep when idle. The dashboard retries up to 30 times at 2.5-second intervals and aborts sibling reads on failure; portfolio invoice/client reads share batched request context and reuse the client activity fit. The scheduled health workflow runs every 10 minutes to reduce cold starts.
- Current hosted data is larger than the original acceptance fixture (2,147 invoices / 52 clients observed); the original 400-invoice synthetic acceptance remains the V1 baseline.
- The active reminder workflow calls Gemini for grounded draft generation and reply classification. The key is managed in Render and ignored local environment files; never print, log, or commit it.
- Live PayPal credentials/payments are not configured. No KYC, PayPal support, or paid AG Grid action is pending for Sandbox V1.

At handoff, branch `codex/late-payer-v1` is at `8bf6675` (`fix: retry transient Gemini generation failures`), pushed to `origin`, with both Render services reporting that commit as deployed. Preserve the user's modified `AGENTS.md`; release documentation changes are being committed separately.

## Next checks

1. Confirm the latest `demo-health.yml` run succeeded; Render deployments and live Gemini draft/reply checks for `8bf6675` have succeeded.
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
