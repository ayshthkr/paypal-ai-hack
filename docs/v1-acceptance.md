# V1 acceptance

## Run locally

Requirements: Bun 1.3.6, Python 3.12/3.13 with `uv`, and Docker Compose.

```sh
make setup
# Set OPERATOR_USERNAME, OPERATOR_PASSWORD, and SESSION_SECRET in .env.
make dev
```

- Dashboard: `http://localhost:3000`
- API docs: `http://localhost:8000/docs`
- Readiness: `http://localhost:8000/ready`
- Reset the synthetic fixture set: `make seed-demo`
- Retrain and evaluate: `make train`
- Run local checks: `make check`
- Read execution/setup checklist: `docs/execution-status.md` and `docs/external-setup.md`

## Verified

- `make setup`: database migration, synthetic seeding and model training succeeded.
- `bun install --frozen-lockfile`: passed.
- `make check`: Ruff passed; **13 tests passed**; ESLint, TypeScript check and Next production build passed. One upstream Starlette/AnyIO deprecation warning was emitted.
- `make train`: produced `lgbm-hazard-v1-seed-20261110` with LightGBM selected. Validation integrated Brier score: 0.1233 versus pooled KM 0.1413. Test score: 0.0476 versus KM 0.2246; paid-only date MAE: 14.37 days over 19 paid invoices. These are synthetic pipeline metrics and do not establish real-world accuracy.
- `make smoke` (final run): health, readiness, public synthetic invoice list/detail and forecast passed.
- Browser journey: forecast and client drift displayed; edited synthetic reminder was approved once and persisted; partial payment updated the balance; full payment set the invoice to `PAID` with USD 0.00 outstanding. The interface confirmed no PayPal email/payment was sent.
- Hosted demo: `https://latepayer-web.onrender.com` displays the explicitly labeled synthetic portfolio, 400 records, LightGBM forecasts, and client drift. The hosted API proxy returns HTTP 200 for readiness, invoices, and clients. Operator login and authenticated session lookup both returned HTTP 200. Synthetic actions never contact PayPal.
- PayPal Sandbox acceptance: OAuth obtained the Invoicing scope; invoice list/search/create, send, and the native reminder endpoint succeeded. A controlled USD 1.00 Sandbox invoice was imported, sent to the allowlisted Sandbox buyer, reminded, then marked paid through the Sandbox external-payment recording API. Sync reconciled it to `PAID`, USD 1.00 paid, and USD 0.00 outstanding. The webhook delivery was resent after fixing nested `resource.invoice.id` parsing; Render recorded HTTP 200, and sync retry refreshed the earlier failed event without errors. These are Sandbox-only operations and moved no real funds.
- A second controlled USD 1.00 Sandbox invoice exercised the hosted approval route: the reminder recommendation was reviewed and approved, the native PayPal reminder was accepted, and the later Sandbox payment reconciled to `PAID` with USD 0.00 outstanding.
- AG Grid is **Community**, free and MIT-licensed. No AG Studio license/key is needed.

## External setup status

PayPal account/KYC and Render setup are complete. No further setup action is required for the Sandbox V1 demo. The source is pushed to private GitHub repo `ayshthkr/paypal-ai-hack`; the free Render Blueprint, Postgres, API, and web service are configured. `https://latepayer-api.onrender.com/ready` returns HTTP 200. Keep `PAYPAL_MODE=sandbox`; no live PayPal send was attempted.
