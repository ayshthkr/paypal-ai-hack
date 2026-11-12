# Late Payer — repo handoff

Read this first when continuing work in another chat. Check `git status` before editing and preserve the user's uncommitted `AGENTS.md` changes.

## What this repo is

Late Payer is a receivables dashboard with payment-date forecasts, descriptive client-delay signals, and operator-approved invoice reminders. Stack: Next.js 16 / React / TypeScript with Bun 1.3.6, FastAPI / Python / `uv`, and Postgres. AG Grid Community is used under its free MIT license. The public dashboard is synthetic-only; provider operations are PayPal Sandbox-only and recipient-allowlisted.

- Hosted demo: <https://latepayer-web.onrender.com>
- API health: <https://latepayer-api.onrender.com/health>
- Local UI/API: <http://localhost:3000> and <http://localhost:8000/docs>

## Current state

### Submission preparation — 2026-11-12

- `docs/hackathon-submission.md` contains paste-ready project text, AI/tool disclosure, sponsor integration details, and a requirement mapping against the official Devpost rules. The public YouTube upload remains pending at the user's request.
- The user authorized MIT licensing and public GitHub visibility. Root `LICENSE` contains MIT; README now foregrounds demo/source links and complete local setup. Before publication, Gitleaks scanned all Git refs and tracked file contents: its only matches were the literal forecast bucket `beyond_180_days`, not credentials. An exact-value check of five locally configured credential values found no matches in historical Git blobs. Ignored environment files remain excluded.
- Submission verification found the existing `TourNavigation` component had been disconnected from the root layout. It is restored under Suspense; a local browser check confirmed Continue opens stop 2. ESLint passed with two existing unused-variable warnings in the screenshot script, TypeScript and the production build passed. Hosted root, API health, and AI configuration status returned HTTP 200; this preparation did not dispatch a PayPal operation.
- GitHub publication completed: anonymous GitHub API reads confirm Public visibility and MIT recognition. Commit `8411d8e` deployed successfully to both Render services. The hosted five-stop check completed in 44.3 seconds with stops 2–5 live and stop 1 using the read-only fallback after its 24-second wait; do not describe that run as all-live. The tour portfolio destination now explicitly selects `#overview` and preserves the hash. The destination and continuation passed a local browser check; lint/typecheck/build passed again.
- Keep the hosted demo available through the end of judging (December 15, 2026). The Render database expiry runbook remains relevant. The video URL placeholder must be replaced before Devpost submission.

### Earlier application release — commit `bf448ef`, 2026-11-12

- Render API and web both report `bf448ef` Live. The API `/health` and hosted web root return 200; `/ready` is not implemented. Startup memory pressure from the Nov 11 incident was addressed in `09e7195`.
- The release implements reviewer workflow fixes across forecasting, collections, replies, actions, scenarios, performance, operator controls, and the packaged fallback. Weekly cash cards reconcile to probability-mass contributors, first-visit queue counts align with ranked actions, filtered and portfolio-wide summaries are labeled, and survival-chart dates and tables remain readable.
- Collections and reply flows retain edits through errors, distinguish save from refresh failures, and provide status reconciliation. Synthetic payment retries reuse stable identities. The packaged fallback is directly reachable from failure states.
- `make check` passed during implementation: Ruff, 96 Python tests, ESLint, TypeScript, and the Next.js production build. The hosted Playwright suite passed 15 checks with two fixture/provider-gated skips. It covers cash-card contributors, empty-search scope, controlled outages, reply timeout recovery, tour/fallback, and collections save/reload.
- The hosted five-stop route completed in 44.5 seconds, under the 180-second budget, with all stops live (23.9, 7.9, 5.9, 1.1, and 1.8 seconds). Render Free can sleep; the route check records a hosted run, not a forced sleep/wake cycle.
- The final 56 ordered hosted screenshots are in `artifacts/screenshots/review-2026-11-12/`; the README records app revision `bf448ef` and confirms no captures were skipped. They include all optional captures, the centered approval dialogs, scenario details, and responsive layouts. PayPal remained in Sandbox, and this implementation/capture pass did not send a reminder or alter a PayPal record.
- The health workflow is enabled and scheduled every five minutes, but recorded scheduled history was sparse. Render Free can sleep; scheduled jobs are best-effort.

### Earlier deployed state and evidence

- Original V1 acceptance covered a 25-client / 400-invoice fixture and a LightGBM survival forecast. Those model metrics and historical test counts are superseded by the active release-candidate facts in `docs/v1-acceptance.md`.
- PayPal Sandbox OAuth/Invoicing access works. Controlled invoice, reminder, payment reconciliation, and webhook delivery were verified. The webhook invoice ID is nested at `resource.invoice.id`; the adapter handles this. Other recent fixes reconcile create responses without IDs and normalize `MARKED_AS_PAID` to `PAID`.
- Hosting behavior: free Render instances can sleep when idle. The dashboard retries up to 30 times at 2.5-second intervals and aborts sibling reads on failure; portfolio invoice/client reads share batched request context and reuse the client activity fit. The scheduled health workflow runs every five minutes to reduce cold starts, but scheduled jobs remain best-effort.
- Current hosted data is larger than the original acceptance fixture (2,147 invoices / 52 clients observed); the original 400-invoice synthetic acceptance remains the V1 baseline.
- The active reminder workflow calls Gemini for grounded draft generation and reply classification. The key is managed in Render and ignored local environment files; never print, log, or commit it.
- Live PayPal credentials/payments are not configured. No KYC, PayPal support, or paid AG Grid action is pending for Sandbox V1.

Application changes through `364a9b7` are pushed to `origin/codex/late-payer-v1`. The earlier release evidence above is historical; consult the submission preparation notes for newer checks. Preserve any existing uncommitted work.

### Repository cleanup — 2026-11-12

- Removed the video script from the published tree at the user's request; the submission retains only the pending video URL.
- Versioned the existing design reference and historical walkthrough audit. The audit predates subsequent fixes and is not a current defect checklist.
- Followed the installed Next.js guidance: stop tracking generated `next-env.d.ts`, ignore it, and run `next typegen` before TypeScript checks so fresh clones remain reproducible and dev/build switching does not dirty Git.

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
