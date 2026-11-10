# Task List 2 implementation

Implemented in the requested order: profile/read path; client fetching and caching; forecast correctness; seed/evaluation; functional workflows; verification. Existing uncommitted continuity documents are preserved separately.

## Read path

- `Server-Timing` and route logs report DB, artifact load, scoring, drift and total elapsed milliseconds. Component timings are inclusive, not additive.
- Startup warms the cached LightGBM artifact. Predictions use one inference thread. Artifact digests distinguish same-day retraining.
- Durable `read_models` projections hold forecasts, client drift and source activity. SQLAlchemy write-transaction hooks refresh affected clients before commit; HTTP and aggregate caches invalidate after commit. Seed, payment, reminder and provider changes participate.
- `/portfolio` combines cards, aging, expected cash and paginated rows using shared batched SQL context. A synchronous SQLAlchemy session is deliberately used serially; concurrent queries on that same session would be unsafe.
- Invoice lists omit curves and detailed facts. Detail endpoints retain the full curve. Activity and forecasts reuse durable projections. A bounded 30-second aggregate cache shares aging and weekly cash across pages.
- Private HTTP caching includes ETags, conditional 304 responses, cookie/session isolation and write invalidation. The proxy forwards conditional headers. Client reads deduplicate for 30 seconds; shared SWR auth avoids navigation reads. Errors never retry 4xx.
- Composite indexes cover status/due date, invoice/payment date and invoice/provider-event date. Client foreign keys and recommendation/attempt foreign keys retain their existing indexes.
- Skeletons, ten-second fetch deadlines, explicit retry, retained previous results and hover prefetch cover slow/cold services. Client search loads on focus rather than blocking the portfolio.

## Forecast and evaluation

- Overdue inference uses a smooth conditional survivor tail, `S(age+t)/S(age)`, instead of repeating an exhausted terminal hazard. Quantile crossings interpolate and unresolved quantiles remain null.
- Far-overdue invoices show “90+ days, low confidence”. A separate Beta-shrunk recovery estimate reserves unrecovered mass and reduces expected cash. This is a sparse-history recovery heuristic, **not an identified probability of eventual recovery**.
- Recorded reminder events determine ladder progression and cooldown. Skipping does not advance the ladder. View flags derive from recorded view timestamps. Display timestamps use UTC and calendar dates use the same date format.
- The seed has 52 clients, 2,154 invoices, exactly 50 open balances, eight deliberately changing clients and three quiet clients. Payment timing includes client effects, lognormal noise, month-end/weekday/holiday effects and occasional disputes. Chronic lateness is distinct from a stable trend.
- Fixed two point-in-time leakage bugs: historical risk rows no longer use final paid balances, and issue-origin forecasts cannot see later views, reminders or partial payments.
- Training retains temporal evaluation cohorts, then refits the deployed model through `AS_OF`. `make train AS_OF=today` remains available. The model footer exposes the version and training cutoff.
- Validation-only split conformal intervals, client-cluster bootstrap confidence intervals, TreeSHAP summaries, currency/conditional-age errors, drift precision/recall/counts/lead times and a clearly labelled reminder simulation appear on the performance page.
- Thin history uses five-settlement shrinkage priors and visible limited-history labels.

### Measured model limits

On the November 11, 2026 held-out synthetic cohort (300 paid invoices), the corrected model has **11-day median absolute error** and **71.3% interval coverage against an 80% target**. These miss the requested 5–10 day illustrative error range and near-80% coverage. Validation-only calibration does not guarantee coverage under the seeded regime shift; test outcomes were not used to tune the correction. Calibration transfer after the full-window refit also needs monitoring. Cox and LightGBM bootstrap intervals overlap; the UI explains that extra complexity is not justified by concordance alone. No real-world accuracy or causal reminder benefit is claimed.

## Workflows

- Server-streamed all-filtered CSV exports include p10/p50/p90 and escape spreadsheet formulas.
- URL state covers status multi-select, due dates, predicted settlement week, client, search, sort, pagination, aging and drift. Predicted-date and drift sorting are available. Bucket/week selection scrolls to the grid; weekly cards group separate currencies and totals explicitly say not converted.
- Client search is keyboard navigable and lazy. Client invoices have URL-backed filters/pagination and a filtered-portfolio link. Unknown invoice/client IDs have visible 404 states; invoice links can be copied. Provider links remain Sandbox-only.
- Queue displays pending/skipped counts, undo-skip and confirmed bulk friendly approval. Edited drafts persist locally immediately and save to the API. Focus moves to another action or the refresh control after review.
- Clock changes precompute projections; historical views display a banner and disable simulation controls. Snapshot reset preserves Sandbox records and has a confirmation and existing rate limit.
- AI drafts and answers persist the facts used in `ai_audits`, expose grounding, and retain template fallback with a three-second drafting deadline.

## Verification

- 54 API tests pass, including pagination bounds, empty views, unknown IDs, full export, ETag invalidation, persistence, reminder history, survival tails, historical balance leakage, seeded no-scoring reads, and snapshot reset under five seconds.
- Ruff, ESLint, TypeScript and production build pass.
- Playwright smoke passes portfolio, URL filtering, lazy client search, queue, client, invoice and 404 navigation; it asserts no browser exceptions, no repeated 404 fetch and no warm API read over two seconds.
- Local PostgreSQL seeded smoke: `/portfolio` 191 ms; `/invoices/demo-invoice-16-045` 191 ms; `/clients` 194 ms; `/facts/clients/demo-client-16` 202 ms; `/action-queue` 206 ms; `/evaluation` 4 ms; `/invoices?view=all` 254 ms. Portfolio response is about 21 KB (roughly 89% smaller than the original 188 KB). These are local measurements, not hosted-service guarantees.

Run `make check`, `make smoke`, and `bun run test:browser` with the API/web servers running. Use `SMOKE_BASE_URL=https://HOST/api` or `PLAYWRIGHT_BASE_URL=https://HOST` for hosted checks. Readiness warming is excluded from the warm-read latency gate.

PostgreSQL snapshot reset completed in 441 ms. Four simultaneous queue requests and four simultaneous portfolio requests all returned 200; atomic proposal/audit inserts prevent duplicates. The browser smoke also verifies date-only rendering in Asia/Kolkata.

The GitHub health workflow is configured for 09:37–19:27 IST and was manually dispatched successfully. Render free services sleep after idle periods; the schedule reduces cold starts during demo hours without changing the hosting plan. See [Render free-service behaviour](https://render.com/docs/free).

Hosted deployment evidence is recorded after the implementation commits.
