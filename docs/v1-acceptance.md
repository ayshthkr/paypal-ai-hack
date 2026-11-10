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
- AG Grid is **Community**, free and MIT-licensed. No AG Studio license/key is needed.

## Not yet verified outside the local demo

- PayPal account/KYC is complete per the user. Correct credentials for the new US Sandbox Business app and controlled Sandbox buyer are configured locally and in Render. The `Late Payer Sandbox` app's Invoicing feature is enabled, but fresh OAuth tokens omit the Invoicing scope and invoice-list/create requests return HTTP 403 `NOT_AUTHORIZED`; no invoice was created or sent. Resolve the Sandbox scope/entitlement, then verify an invoice import, reminder, and payment. The invoice webhook is registered and configured in Render, but actual delivery is not verified.
- The source is pushed to private GitHub repo `ayshthkr/paypal-ai-hack`; Render is connected to that repository. The free Blueprint, Postgres, API, and web service are configured. `https://latepayer-api.onrender.com/ready` returns HTTP 200 with the database, production operator credentials, Sandbox credentials, model artifact, and webhook ID configured. The database has 25 synthetic clients and 400 invoices, seeded once without resetting later data. The hosted browser dashboard has been verified.
- See [the setup checklist](external-setup.md) for the exact remaining actions. Keep `PAYPAL_MODE=sandbox` during acceptance. No live PayPal send was attempted.
