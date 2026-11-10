# External setup checklist

PayPal account/KYC and Render account setup are marked done based on your latest notes. Correct Sandbox REST app credentials are saved in the ignored local `.env`, and OAuth was verified. Secrets are not in source control or chat.

## One-time local setup

- [x] Bun 1.3.6, Python 3.13 with `uv`, and Docker Compose are installed: [Bun installation](https://bun.sh/docs/installation).
- [x] `make setup` passed: locked Bun dependencies, local Postgres, migrations, synthetic demo data, and model training are ready.
- [x] Non-default local `OPERATOR_USERNAME`, `OPERATOR_PASSWORD`, and random `SESSION_SECRET` are configured in ignored `.env` (file mode `0600`).
- [x] `make dev` is running; UI is at `http://localhost:3000` and API docs at `http://localhost:8000/docs`.

## PayPal

- [x] PayPal account and identity verification/KYC: done per your latest note. No further PayPal account/KYC action is listed.
- [x] New US Sandbox Business merchant and `Late Payer Sandbox` REST app created; corrected Client ID and Secret saved in local `.env`; OAuth verification passed. `PAYPAL_MODE=sandbox`.
- [x] Add the existing controlled Sandbox Personal buyer email to `PAYPAL_ALLOWED_RECIPIENT_EMAILS` in local `.env`.
- [x] Existing controlled Sandbox Personal account selected for testing; the email is stored locally in the allowlist, not shown here.
- [x] In PayPal Developer, enable and save **Invoicing** for the `Late Payer Sandbox` app.
- [x] Confirm Sandbox Invoicing API access: fresh OAuth returned the Invoicing scope, and invoice list/search/create succeeded with the configured app/account. No Developer Support or entitlement request was needed.
- [x] Register `https://latepayer-api.onrender.com/webhooks/paypal` in that app for invoice paid, cancelled, refunded, and sent events; the webhook ID is stored in Render and ignored local `.env.render`.
- [x] Complete the controlled USD Sandbox invoice journey: import, send, native reminder, recorded Sandbox payment, reconciliation to `PAID` / USD 0.00 outstanding, and verified webhook delivery (Render HTTP 200).

No further PayPal action is needed for Sandbox acceptance. The dashboard has `make seed-sandbox`, `make sync`, and `make record-payment ...` commands for the controlled test cycle. Live credentials and live payment operations are intentionally not configured or used.

### PayPal India KYC

PayPal India lists identity confirmation for the business type, PAN, an Indian bank account, and a purpose code for international payments; IEC may apply when exporting goods. Exact items depend on account and transaction type. You reported the PayPal setup/KYC as complete. None of this KYC is required for local or Sandbox app testing. See [PayPal India receiving payments](https://www.paypal.com/in/cshelp/article/how-do-i-receive-payments-through-paypal-help667).

## Render

- [x] Render account setup: done per your note.
- [x] Code pushed to private GitHub repository `ayshthkr/paypal-ai-hack` on branch `codex/late-payer-v1`.
- [x] GitHub sign-in completed; Render's GitHub App is restricted to the single private `ayshthkr/paypal-ai-hack` repository.
- [x] Render GitHub connection and free Blueprint from `render.yaml` are configured. The API is at `https://latepayer-api.onrender.com`; Postgres is configured.
- [x] API environment contains a generated `SESSION_SECRET`, generated operator username/password, Sandbox credentials, the controlled-recipient allowlist, and the registered webhook ID. The ignored local `.env.render` holds the generated operator login (mode `0600`). Keep `PAYPAL_MODE=sandbox` for acceptance.
- [x] Confirm the API `/ready` endpoint returns HTTP 200 with its database, operator, Sandbox credentials, model artifact, and webhook ID configured; the web root returns HTTP 200.
- [x] Deploy the first-boot-only synthetic fixture loader. Hosted dashboard verified with 400 invoice records, LightGBM forecasts, and client drift; simulated actions are labeled and do not call PayPal.
- [x] Verify hosted operator login and authenticated session through the web API proxy; generated credentials remain in ignored local `.env.render`.
- [x] Complete the hosted PayPal Sandbox invoice/reminder/payment and webhook-delivery acceptance flow.

## AG Grid

- [x] Free option selected: AG Grid Community (MIT). It needs no license key or paid AG Studio account. The survival curve is a custom SVG component in the app.

## Not configured in this workspace

- Live PayPal credentials and live payment operations are intentionally not configured. Sandbox V1 acceptance is complete.
