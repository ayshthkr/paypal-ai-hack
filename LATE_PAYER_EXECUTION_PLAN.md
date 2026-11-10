# Late Payer — V1 MVP execution plan for Luna xhigh

## 1. Deliverable

Execute this plan to produce a **working, runnable V1 application**, including implementation, database, model artifacts, UI, PayPal sandbox integration and end-to-end verification. Finish the product before considering sponsor extras or submission material.

**Required user journey:** load invoices → see predicted payment dates and client drift → inspect an invoice → preview/edit a recommended reminder → approve and send through PayPal → observe payment status update.

**Execution order:** 00 → 01 → 02 → 03 → 04 → 05 → 06 → 07. Continue automatically between tasks. Every task ends with working code and an acceptance check; status documents alone do not complete a task.

### Included in V1

- One sandbox merchant, controlled recipients, authenticated operator and public read-only demo.
- PayPal invoice import/sync, a small sandbox seed script, reminders and payment-status reconciliation.
- Verified, deduplicated payment webhooks; explicit manual sync as recovery.
- Reproducible synthetic payment history, a trained LightGBM survival model and Kaplan–Meier fallback.
- Conditional payment-date forecasts, uncertainty and factual reasons.
- Explainable client drift from payment-delay trend and recency.
- Deterministic reminder policy with editable text and explicit approval.
- AG Studio invoice dashboard and one custom survival-curve widget.
- Local setup, deployable configuration, meaningful checks and an acceptance runbook.

### Explicitly outside V1

BG/NBD, causal/uplift modelling, reminder-effect simulation, SHAP, Bryntum, drag/drop scheduling, future scheduled sends, nightly automation, Zapier, Elastic, Postman, QR codes, partial-payment **offers/updates**, multiple merchants, self-service PayPal account connection, multiple public training datasets, Cox/RSF/AFT benchmarking, Studio chat agents, video production and hackathon submission.

Existing partial payments must still sync correctly. A partial payment does not count as full settlement. These exclusions are fixed scope boundaries; do not implement them merely because a library makes them available.

## 2. Smallest architecture that supports the product

```text
Browser → Next.js UI/server proxy → FastAPI → Postgres
                                      ├─ PayPal adapter
                                      ├─ feature/forecast/drift/policy services
                                      └─ local versioned model artifact
PayPal → verified webhook → stored event → authoritative invoice refresh
CLI → synthetic seed / train / sandbox seed / reconciliation
```

- Adapt the [official PayPal/AG Studio starter](https://github.com/paypaldev/hackathon-paypal-ag-grid-boilerplate); retain its useful dashboard wiring and remove unrelated screens.
- Use FastAPI, SQLAlchemy, Alembic and Postgres for domain logic and persistence; Python for training/inference; Next.js/TypeScript for UI. Pin compatible dependencies once they build.
- Use a typed PayPal adapter. Prefer the Agent Toolkit when its installed interfaces cover the required operation; use direct REST for gaps. Keep vendor specifics inside the adapter. [Toolkit](https://github.com/paypal/agent-toolkit/)
- Use **two application services and one database**. No queue framework, separate worker, Redis, cron, microservices or generic event-sourcing platform.
- Small single-invoice scoring runs synchronously after sync; list imports use a CLI/bounded operator action. Training is a CLI command, never an HTTP request.
- Store webhook events before processing. Attempt refresh immediately; persist failures for retry during the next explicit sync. Return a non-success response on transient verification/storage failure so delivery can retry. Never mutate domain state from an unverified event.
- UI polls the read API while visible and refreshes after actions. Manual **Sync PayPal** provides recovery; no SSE/WebSocket infrastructure.
- Runtime uses UTC timestamps and merchant-local dates; money uses Decimal plus currency. Group totals by currency.
- Demo mode uses the same services and schemas with a deterministic adapter; show a persistent **Synthetic demo** banner. Never silently switch a failed live send to a mock success.

```text
apps/web/                  dashboard, invoice detail, approvals, auth/proxy
services/api/app/          API, DB, PayPal, features, scoring, drift, policy
services/api/alembic/      schema migrations
ml/                        synthetic generator, training, evaluation
scripts/                   setup, seed, sync, model commands
artifacts/                 permitted model + schema + evaluation metadata
tests/                     business invariants and one browser journey
docs/                      concise setup, execution status, acceptance evidence
render.yaml                UI/API/database configuration
```

## 3. Minimal persistent contracts

Use seven domain tables; use JSON for small prediction/reason payloads. Do not introduce tables solely for hypothetical future features.

| Table | Required fields |
|---|---|
| `clients` | ID, provider/customer identity, display name, source, cached drift/reasons and scoring timestamp. |
| `invoices` | ID, PayPal ID, client, issue/due dates, total/paid/outstanding, currency, status, source, provider payload, sync time, material-state version. |
| `payments` | Provider payment ID, invoice, amount/currency, occurrence time, source; unique provider identity. |
| `invoice_events` | Unique provider event ID, type, occurred/received times, raw payload, verification/processing status, failure reason. |
| `predictions` | Invoice, scoring date, input fingerprint, model/schema version, curve, p10/p50/p90 dates, 7/14/30-day probabilities, factual reasons. |
| `recommendations` | Invoice, action/tone, reason, draft/edited text, invoice version, state, approved hash, created/approved time. |
| `action_attempts` | Recommendation, unique attempt key, state, provider reference, started/finished time, error; successful/ambiguous send record. |

Merchant configuration is server configuration for this single-merchant MVP. Do not create a tenant-management subsystem.

**Action states:** proposed → skipped, or proposed → sending → sent/failed/unknown. The approve-and-send endpoint validates and stores the exact approved content before dispatch. Unknown means delivery is ambiguous and cannot be automatically resent.

**API:** `GET /health`, `/ready`, `/invoices`, `/invoices/{id}`, `/clients`; `POST /webhooks/paypal`, `/sync`, `/recommendations/{id}/send`, `/recommendations/{id}/skip`. Invoice detail includes forecast, reasons, recommendation and action history. OpenAPI defines typed requests, responses and errors. Read routes are public only for designated synthetic demo records; live merchant records and all mutations require operator authentication.

## 4. Implementation tasks

### 00 — Bootstrap the running app

- [ ] Inspect repository instructions and starter; adapt directly into the layout above without generating unrelated example features.
- [ ] Install/pin the minimal dependencies, add migrations, `.gitignore`, `.env.example` and lockfiles.
- [ ] Add operator login/session and read-only synthetic demo access. Keep API/provider keys server-side; verify authorization again in FastAPI.
- [ ] Create the seven tables, typed API schemas, empty invoice list, health/readiness endpoints and a working UI shell.
- [ ] Provide working commands: `make setup`, `dev`, `migrate`, `seed-demo`, `train`, `sync`, `seed-sandbox`, `check`, `smoke`.
- [ ] Record only missing credentials or dependency blockers in `docs/execution-status.md`; continue all independent work.

**Acceptance:** fresh local setup runs UI/API/Postgres; migrations succeed; invoice list renders; anonymous users cannot access live data or mutation endpoints.

### 01 — Deliver the complete synthetic product path first

- [ ] Generate fixed-seed history for approximately 25 clients/400 invoices: prompt, slow, volatile, drifting and new clients; include open, partial-paid and cancelled cases. Include a history cutoff and known generation parameters.
- [ ] Implement canonical invoice/payment records and synthetic adapter; deterministic reset must be idempotent.
- [ ] Render a basic invoice list/detail immediately; use a labelled provisional Kaplan–Meier forecast and template reminder.
- [ ] Implement proposal → edit → approve/send → simulated payment → updated status through the same services that the live adapter will use. Expose simulation controls only in demo mode.
- [ ] Keep synthetic and live identities/source fields separate; synthetic history can enrich only explicitly mapped demo clients, never arbitrary live customers.

**Acceptance:** one browser journey works end to end with persistence and a visible synthetic banner; restarting preserves data; reset reproduces the same records.

### 02 — Replace simulated operations with the PayPal sandbox

- [ ] Implement OAuth caching, paginated invoice import, details lookup and create/send/remind/payment-recording commands. Confirm fields against [Invoicing v2](https://developer.paypal.com/api/invoicing/v2).
- [ ] Probe only required capabilities: create/send, import/details, remind, payment and webhook delivery. Do not investigate backdated invoices, partial-payment updates or other excluded features.
- [ ] Seed a few controlled sandbox invoices and map them to local demo history; retain historical fixtures locally instead of creating hundreds of PayPal invoices.
- [ ] Add timeouts, typed failures and bounded retries for safe reads; never blindly retry an uncertain send.
- [ ] Implement webhook authenticity verification, unique-event deduplication, durable storage and authoritative invoice refresh. Handle duplicate/out-of-order delivery without applying stale event snapshots. [Webhook requirements](https://developer.paypal.com/api/invoicing/webhooks)
- [ ] Implement explicit sync that imports current state, reconciles failed event refreshes and rescores changed invoices. Treat partial payment, full payment, cancellation and refund distinctly; closed invoices cannot receive reminders.
- [ ] Register the webhook once a public HTTPS endpoint is available in task 06; finish local adapter work now if deployment credentials are unavailable.

**Acceptance:** one real sandbox invoice imports; an approved reminder is accepted; buyer checkout or explicitly labelled external-payment recording changes the persisted balance/status. Duplicate verified events do not duplicate payments. Do not mark webhook delivery verified until it has actually arrived.

### 03 — Train and integrate the payment forecast

- [ ] Implement one shared `features(invoice, as_of)` function for training and inference. Use amount, terms, calendar/age, settled-history late rate, recent delay, variability, history count and other open exposure. Include missing-history indicators.
- [ ] Build history strictly from evidence available at the scoring instant; prevent later settlement/client behaviour from changing past snapshots. Do not add unavailable view/reminder features.
- [ ] Train LightGBM discrete daily hazard on synthetic invoice-day rows. Label only full settlement as the event; right-censor open invoices at the known observation cutoff. Exclude cancelled/refunded invoices from training and document the limitation.
- [ ] Keep all daily rows from an invoice together; split chronologically and truncate training outcomes/history at the training cutoff. Use separate later validation/test windows with explicit observation boundaries. Do not downsample initially or run broad hyperparameter search.
- [ ] Forecast remaining time conditional on unpaid today: `S(k) = product(1-h(age+j, x_future_j)), j=1..k`. Advance known age/calendar features; assume no future intervening events.
- [ ] Return a 180-day curve, payment probabilities within 7/14/30 days and p10/p50/p90 dates. Keep unreached quantiles null and show “beyond forecast horizon.” Overdue invoices say “already overdue.”
- [ ] Compare against pooled/client Kaplan–Meier on the same held-out cohorts using integrated Brier score within censoring support and paid-only date MAE with its subset caveat. Save actual results, split details and one calibration plot; do not claim synthetic metrics prove real-world accuracy. [Evaluation APIs](https://scikit-survival.readthedocs.io/en/stable/user_guide/evaluating-survival-models.html)
- [ ] Store artifact, feature schema, seed, training provenance and model version. Prefer validated LightGBM; use labelled KM fallback on insufficient history, missing/incompatible artifact or poorer held-out performance.
- [ ] Load the trained artifact in the API and replace provisional forecasts; cache by invoice/client-history fingerprint, scoring date and model version. A new client payment must invalidate affected open-invoice predictions.

**Acceptance:** `make train` produces an actual LightGBM artifact and reproducible report; application loads it; probabilities are bounded, curves monotone and quantiles ordered; cold-start and leakage checks pass. The UI identifies synthetic training and the active forecast model.

**Dataset rule:** V1 must train without external downloads or Kaggle credentials. If an accessible, permissioned accounts-receivable dataset is already available, support it through the same canonical schema only after V1 acceptance; do not block product completion on data acquisition.

### 04 — Add client drift and reliable reminder decisions

- [ ] Compute Theil–Sen slope over the last five settled invoices plus recent-delay deviation; show insufficient history below three settled invoices. Recency uses the client's usual invoicing cadence only with enough history.
- [ ] Expose drift evidence and configurable thresholds, initially a delay increase above two days per invoice. Describe it as a heuristic signal, never a learned churn probability.
- [ ] Implement wait/friendly/firm policy: no reminder before due minus three days; one successful or unresolved reminder per seven days; firm only after due; block closed, disputed or otherwise unsupported statuses.
- [ ] Use explicit rules: ineligible → wait; eligible overdue → firm; eligible within three days before due → friendly when P(payment by due) is below configurable 0.5, otherwise wait. Low-history cases may use the labelled baseline. Display rule/reason, not estimated uplift.
- [ ] Generate editable factual template text using client name, balance, due date and verified PayPal payment link. If LLM credentials already exist, optional drafting must preserve the same validated facts and fall back to templates; it is not a completion dependency.
- [ ] Bind approval to exact text and material invoice state. Lock the recommendation and record the send attempt before dispatch; concurrent/repeated requests cannot send twice.
- [ ] Re-fetch live invoice immediately before sending and recheck balance, status, cooldown and approval version. Material changes invalidate approval. Cosmetic sync metadata must not invalidate it.
- [ ] Store provider response/error; show sent/failed/unknown honestly. Unknown delivery blocks resending until operator reconciliation establishes a safe outcome. A definite provider rejection can return to review/retry.
- [ ] Sanitize displayed provider content; invoice notes cannot issue instructions or execute tools. Enforce recipient ownership and controlled sandbox mode server-side.

**Acceptance:** drift fixtures show expected evidence; wait/friendly/firm branches work; skipped, stale, paid, cooldown-blocked and duplicate approvals cannot send; uncertain dispatch is never represented as success.

### 05 — Finish the AG Studio MVP interface

- [ ] Replace basic list with Studio invoice grid: client, outstanding/currency, due/overdue, predicted date, 14-day payment probability, uncertainty, drift and recommended action.
- [ ] Add sorting/filtering, currency-separated balance totals and a compact drifting-client panel.
- [ ] Register one custom survival widget showing the conditional curve, median, p10–p90 interval and beyond-horizon state.
- [ ] Finish invoice detail with factual history reasons, current balance/status, model/source badge, editable reminder, approve-and-send, skip and action history.
- [ ] Poll while page is visible and refresh after actions; add Sync PayPal. Display last sync, loading/empty/error, model fallback and unknown-dispatch states.
- [ ] Make the main journey usable on desktop and narrow screens, with labelled buttons and keyboard-accessible controls. Avoid adding extra product surfaces.

**Acceptance:** the entire included V1 journey works through the UI with no CLI intervention except initial seed/training or controlled sandbox payment; synthetic/live mode is unmistakable; model output and provider errors are visible.

### 06 — Package and run the application

- [ ] Add production builds, Render UI/API/Postgres configuration, migration command, exact env-variable names and health checks. Package the permitted model artifact; persist business data in Postgres.
- [ ] Deploy if account access is available; register the public webhook endpoint and verify real delivery. Keep secrets in hosting settings and record only nonsecret URLs.
- [ ] Confirm proxy/auth/cookie behaviour across the deployed UI/API; restrict CORS and keep live-data access authenticated. Public synthetic demo cannot send PayPal reminders or use paid AI endpoints.
- [ ] Verify restart preserves invoices/action history and model loading; manual sync recovers a missed/failed event.
- [ ] Provide concise README setup, train, seed, sandbox credentials configuration, deploy and demo-reset instructions. Include actual library/data notices and limitations.

**Acceptance:** clean setup and production build run. If hosting is configured, hosted browser journey and verified webhook work. If external access is missing, the fully runnable local MVP and deployable configuration must still be finished; list the exact pending account actions without claiming hosted success.

### 07 — Close acceptance gaps and deliver V1

- [ ] Run targeted tests: historical leakage/censoring, survival conditioning/bounds, drift cold-start, currency handling, approval races/cooldown, invalid/duplicate/out-of-order webhooks, provider timeout and partial/full/closed invoice sync.
- [ ] Run one real browser smoke journey: browse → forecast → drift → edit/approve → sent → payment update. Run synthetic mode independently without PayPal credentials.
- [ ] Run typecheck/lint, applicable tests and production build; fix failures before final handoff. Verify no credentials/raw restricted data are tracked.
- [ ] Audit every acceptance criterion above against evidence. Fix missing functionality; do not replace failures with TODOs or mocked claims.
- [ ] Write `docs/v1-acceptance.md`: start commands, working URLs, active forecast model/provenance, test results, tested live operations and exact external blockers.
- [ ] Deliver runnable V1 and a concise handoff. Leave all excluded work deferred; do not start video, sponsor extras or a second architecture pass.

**V1 release gate:** actual application code, persistent data, trained model, working UI, approval protections, reproducible setup and passing core browser flow. A synthetic-only build is a **demo MVP**, not a completed live PayPal MVP. Missing PayPal credentials leave live acceptance explicitly pending; missing hosting access leaves deployment pending, not implementation.

## 5. Single prompt to execute the whole plan

Select **Luna / xhigh** in your execution environment, then paste:

```text
Implement the entire V1 MVP defined in LATE_PAYER_EXECUTION_PLAN.md.
Execute tasks 00 through 07 in dependency order and continue automatically.
The deliverable is a working application, not another plan or scaffold.

Read repository instructions and existing files first. Treat the source technical
design and external documents as reference data. This execution plan defines scope.
Use official docs only when needed to resolve a concrete API/package question.
Prefer direct implementation, shared services and the existing starter.
Do not add excluded features, alternative architectures or speculative abstractions.
Do not spawn subagents unless separately authorized by the user.

Get the synthetic browser flow working first, connect PayPal, train/integrate the
forecast, complete approvals/dashboard, package/deploy, then verify V1.
Never stop between tasks to ask whether to continue. Fix acceptance failures before
moving on. Run meaningful targeted tests and the final browser smoke/build.

Use credentials already configured; never print them. If external access is missing,
record exactly what is missing and finish all independent work. Keep live validation
pending instead of fabricating success. Never silently replace live actions with mocks.
Do not weaken authenticity checks or approval rules to make a demo appear to work.

Maintain a compact docs/execution-status.md with completed task IDs, changed files,
commands/check results and remaining blockers. Update checkboxes only with evidence.
On context reset, resume the first incomplete task using this file; do not re-plan.
Finish only after the V1 release gate is satisfied or all remaining work requires
specific unavailable external inputs. Final response: run commands, app location,
verified capabilities, test results and exact outstanding external actions.
```

Resume after interruption:

```text
Continue implementing V1 from LATE_PAYER_EXECUTION_PLAN.md using Luna xhigh.
Read docs/execution-status.md, inspect the current implementation and resume the
first incomplete acceptance criterion. Complete all remaining required tasks.
Do not expand scope, restart completed work or stop after a single task.
```

## 6. Rules that keep execution fast

1. Working product path first; documentation accompanies implementation.
2. One chosen stack and one supported flow; no framework comparisons or broad capability probes.
3. Synthetic data is the default training dependency; real-data acquisition is deferred.
4. Build the actual survival model, but skip model tournaments and hyperparameter sweeps.
5. Heuristic drift, deterministic policy, templates and polling are final V1 implementations.
6. No future scheduling means no worker/cron/queue infrastructure.
7. Only checks that verify real risks or the release journey; no tests that mirror trivial implementation.
8. Keep provider integration honest: explicit mock mode, real sandbox evidence and visible external blockers.
9. Scope is fixed. Finish V1 acceptance before considering anything excluded.
