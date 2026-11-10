# Execution status

## Completed locally

- **00 Bootstrap:** Bun 1.3.6 lockfile, Python `uv` lockfile, Postgres, seven-table Alembic schema, FastAPI/Next app, operator sessions, public synthetic-only reads, `/health` and `/ready`.
- **01 Synthetic journey:** deterministic 25-client/400-invoice data, partial/full/cancelled cases, LightGBM/Kaplan–Meier forecasts, editable approval and simulated payment updates. Browser smoke completed against `demo-invoice-01-12`: edited reminder approved, local action persisted, partial payment reconciled, then invoice reached `PAID` with zero outstanding. No PayPal call was made.
- **02 PayPal adapter implementation:** Sandbox-only OAuth/REST adapter, paginated invoice sync, detail fetch, controlled invoice creation/send, native reminder, allowlisted external payment-recording CLI, bounded safe-read retries, ambiguous mutation handling, webhook verification/storage/deduplication, and authoritative invoice refresh. Sandbox credentials and the existing Personal test-buyer allowlist are saved in ignored local `.env`; OAuth passed without displaying keys or token. PayPal allowed creation/import of a USD draft but rejected sending it with `INR_FOREIGN_CURRENCY_BLOCKED`; an INR create attempt was rejected by the API schema. Reminder and payment-recording checks are pending.
- **03 Forecast:** deterministic synthetic history, point-in-time features and censoring, trained LightGBM hazard artifact, Kaplan–Meier fallback, conditional 180-day forecasts, quantiles/probabilities, calibration plot and held-out synthetic report.
- **04 Decisions:** client drift evidence, deterministic wait/friendly/firm policy, cooldown and invoice-state rechecks, exact-text approval hash, idempotent attempts and unknown-delivery handling.
- **05 UI:** AG Grid Community (free MIT edition), invoice sort/filter, currency totals, uncertainty and drift fields, custom SVG survival curve, invoice review, action history, demo payment controls, visible synthetic labels and refresh polling.
- **06 Packaging:** local and Render Blueprint configuration, server-side secrets, health check, migration startup, Bun production build and setup documentation. Production API refuses readiness and login when operator credentials/session secret are defaults.
- **07 Verification:** final `make check` passed (Ruff, 13 pytest tests, ESLint, TypeScript, production build); final `make smoke` passed; `make train` regenerated the LightGBM artifact/report; browser demo approval and partial/full payment journey passed. `bun install --frozen-lockfile` and `make setup` passed earlier in this run. `.env` contains non-default local operator credentials and Sandbox config, is mode `0600`, and is ignored by Git.

## External work still required

1. PayPal account/KYC is marked complete based on the user's latest note. Sandbox REST credentials and a controlled Sandbox Personal test buyer are configured locally; OAuth passed. The India Sandbox merchant's foreign-currency send restriction blocks live reminder acceptance; check whether a no-email payable invoice/payment record is supported or use a Sandbox merchant account with compatible currency settings.
3. Render account setup is marked complete, but the open dashboard shows the service-creation onboarding and this repository has no Git remote. Connect/deploy the repo as a Render Blueprint, configure operator and PayPal Sandbox secrets in Render, then register the public API webhook and verify one delivery.
4. Hosted browser journey and PayPal webhook delivery are not claimed as tested. No live PayPal send was attempted.

## Main changed areas

- App and proxy: `app/`, `services/api/app/`
- Schema/model/data: `services/api/alembic/`, `ml/`, `artifacts/`
- Operations: `Makefile`, `scripts/`, `render.yaml`
- Setup/acceptance: `README.md`, `docs/external-setup.md`, `docs/v1-acceptance.md`
- Tests: `tests/`
