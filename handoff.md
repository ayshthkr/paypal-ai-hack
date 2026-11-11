# Late Payer — repo handoff

Read this first when continuing work in another chat. Then check `git status` before editing; the current working tree has intentional uncommitted documentation/config changes.

## What this repo is

Late Payer is a receivables dashboard with payment-date forecasts, descriptive client-delay signals, and operator-approved invoice reminders. Stack: Next.js 16 / React / TypeScript with Bun 1.3.6, FastAPI / Python / `uv`, and Postgres. AG Grid Community is used under its free MIT license. The public dashboard is synthetic-only; provider operations are PayPal Sandbox-only and recipient-allowlisted.

- Hosted demo: <https://latepayer-web.onrender.com>
- API/readiness: <https://latepayer-api.onrender.com/ready>
- Local UI/API: <http://localhost:3000> and <http://localhost:8000/docs>

## Current state

### Active release candidate — 2026-11-11

- Local verification passes: Ruff, 83 Python tests, ESLint, TypeScript, and the Next.js production build. Deployment verification is pending this candidate's commit.
- Runtime model selection now compares LightGBM, Kaplan–Meier, and Cox PH on validation IPCW integrated Brier; the checked-in release artifact selects Cox PH (`cox-ph-v7-seed-20261110-asof-2026-11-11`). `artifacts/eval.json` separately reports the complete deployed scoring-path replay and synthetic-data limitations.
- Cash summaries use probability-weighted settlement mass consistently, and disputed/unforecastable open balances reconcile into a separate gross exposure bucket. Hypothetical terms scoring now uses new issue/due dates.
- Public synthetic mutations are isolated per browser session, and `/offline-demo` serves a generated, read-only snapshot. Dashboard clock changes are committed with their data; action and cash exposure are above the fold.
- Reply triage has a persisted preview/correct/approve/dismiss flow and dispute resolution. Collections review stores owner, notes, follow-up date, and resolution. PayPal reminder screens disclose that the provider sends its native email; edits remain internal notes.
- Alerts exclude habitual lateness and require stronger recent-worsening/outlier evidence. The generated synthetic backtest has 9 flags and 5 generator-defined positives; it does not establish real-world precision.
- `/ai/status` reports safe provider/model/configured metadata, while draft responses identify source and grounding facts. After deployment, record the live status and a successful grounded draft without exposing the key. Render's `GEMINI_API_KEY` is dashboard-managed; never copy it into source or logs.
- The scheduled `.github/workflows/demo-health.yml` workflow runs every 10 minutes, checks the core routes and AI status, and keeps both Render services warm. Confirm its first successful run after the commit.

### Earlier deployed state and evidence

- Original V1 acceptance covered a 25-client / 400-invoice fixture and a LightGBM survival forecast. Those model metrics and historical test counts are superseded by the active release-candidate facts in `docs/v1-acceptance.md`.
- PayPal Sandbox OAuth/Invoicing access works. Controlled invoice, reminder, payment reconciliation, and webhook delivery were verified. The webhook invoice ID is nested at `resource.invoice.id`; the adapter handles this. Other recent fixes reconcile create responses without IDs and normalize `MARKED_AS_PAID` to `PAID`.
- Latest operational issue: the free Render API's cold start outlasted the UI's former five retries. The dashboard now retries up to 30 times at 2.5-second intervals and aborts sibling reads when a request fails. Portfolio invoice/client reads now share batched request context; the client activity fit is reused across concurrent dashboard calls.
- Latest commits, pushed to `origin/codex/late-payer-v1`: `9e1359d` (portfolio reads), `11989f0` (reuse activity fit), `a7af8e7` (abort sibling requests). Ruff, ESLint, and TypeScript checks passed for this fix; the full test suite was not rerun.
- Live follow-up: hosted bundle contains the retry/cancellation changes; `/ready` returned HTTP 200. Concurrent web proxy reads for invoices and clients both returned HTTP 200 in about 13 and 12 seconds. This is improved and working after wake-up, but portfolio reads remain slow on the free service. The latest GitHub status was `pending` with no reported checks.
- Current hosted data is larger than the original acceptance fixture (2,147 invoices / 52 clients observed); the original 400-invoice synthetic acceptance remains the V1 baseline.
- Last full `make check` passed 16 tests plus lint, typecheck, and production build; `make smoke` passed. Those full checks predate the latest performance fix. The Gemini settings loader was checked separately.
- `GEMINI_API_KEY` is present in local ignored `.env` (mode `0600`) and is represented by an empty placeholder in `.env.example`. `Settings` exposes it, but no current feature calls Gemini. It is not configured in Render. Never print or commit the key.
- Live PayPal credentials/payments are not configured. No KYC, PayPal support, or paid AG Grid action is pending for Sandbox V1.

At handoff, branch `codex/late-payer-v1` is at `a7af8e7` (`fix: cancel sibling dashboard requests on failure`), pushed to `origin`. Deployment is observed through the updated hosted bundle and successful live API reads; check Render/GitHub status before claiming a completed deployment. Preserve existing working-tree changes: modified `AGENTS.md`, `README.md`, and `docs/v1-acceptance.md`, plus untracked `handoff.md` and `walkthrough.md`. These handoff edits are uncommitted too.

## Next checks

1. Check Render's latest deploy/build result and GitHub status for `a7af8e7`.
2. Confirm a cold-start dashboard load now recovers within the retry window; current concurrent invoice/client reads still take 12–13 seconds.
3. Run the full verification suite after the performance commits if continuing code changes; the last full check predates them.

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
