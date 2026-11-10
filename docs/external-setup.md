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
- [ ] Resolve the Sandbox Invoicing API permission: the app toggle is enabled, but a fresh OAuth token omits `https://api.paypal.com/v1/invoicing:*`, and invoice list/create requests return HTTP 403 `NOT_AUTHORIZED`. Ask PayPal Developer Support to enable the Sandbox Invoicing scope/entitlement for this app/account, then retry `make sync` and `make seed-sandbox`.
- [x] Register `https://latepayer-api.onrender.com/webhooks/paypal` in that app for invoice paid, cancelled, refunded, and sent events; the webhook ID is stored in Render and ignored local `.env.render`.
- [ ] Seed/send one controlled USD Sandbox invoice, complete its Sandbox payment, and confirm the webhook delivery and invoice refresh after the Invoicing API permission is fixed.

The remaining PayPal action is resolving the missing Sandbox API scope, then completing the controlled invoice/payment and webhook-delivery check—not account verification or KYC. The dashboard has `make seed-sandbox`, `make sync`, and `make record-payment ...` commands for the controlled test cycle.

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
- [ ] Complete the operator-authenticated hosted journey and the Sandbox buyer payment/webhook-delivery check after PayPal grants the missing Invoicing API scope.

## AG Grid

- [x] Free option selected: AG Grid Community (MIT). It needs no license key or paid AG Studio account. The survival curve is a custom SVG component in the app.

## Not configured in this workspace

- [ ] PayPal Sandbox Invoicing API scope is still denied despite the app feature being enabled; the end-to-end Sandbox invoice/payment and webhook delivery run is pending.
- [ ] Complete the PayPal Sandbox invoice/payment/webhook journey after Developer Support resolves the missing Invoicing API scope.
