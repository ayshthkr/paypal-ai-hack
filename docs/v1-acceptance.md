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
- AG Grid is **Community**, free and MIT-licensed. No AG Studio license/key is needed.

## Not yet verified outside the local demo

- PayPal account/KYC is complete per the user. Correct credentials for the new US Sandbox Business app and an existing controlled Sandbox Personal buyer are configured in local `.env`, and Sandbox OAuth passed. The app's Invoicing capability is disabled, so the first invoice operation returned `NOT_AUTHORIZED` without creating or sending an invoice. Enable Invoicing, then verify a Sandbox reminder/payment. Webhook delivery is still pending deployment.
- The source is pushed to private GitHub repo `ayshthkr/paypal-ai-hack`. GitHub sign-in is complete and Render's GitHub App is scoped to that repository only, but Render's Git deployment credential is not yet connected in the dashboard. Hosted readiness and the hosted browser journey remain pending.
- Complete these external steps in [the setup checklist](external-setup.md). Keep `PAYPAL_MODE=sandbox` during acceptance; after deployment, register `https://<API_HOST>/webhooks/paypal`, configure its webhook ID, and verify a real Sandbox delivery.
