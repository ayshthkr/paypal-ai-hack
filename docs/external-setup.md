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
- [ ] In PayPal Developer, open **Apps & Credentials → Late Payer Sandbox → Features**, enable **Invoicing**, then save. This is an app-level Sandbox permission; the API currently returns HTTP 403 and no invoice was created or sent.
- [ ] After deploying the API, register `https://<API_HOST>/webhooks/paypal` in that PayPal app for `INVOICING.INVOICE.PAID`, `INVOICING.INVOICE.CANCELLED`, `INVOICING.INVOICE.REFUNDED`, and `INVOICING.INVOICE.SENT`; copy its Webhook ID to `PAYPAL_WEBHOOK_ID`.
- [ ] Seed/send a controlled USD Sandbox invoice, complete its Sandbox payment, and confirm the webhook delivery and invoice refresh.

The remaining PayPal items are enabling app-level Invoicing, completing the controlled Sandbox invoice/payment check, and deployed webhook registration—not account verification or KYC. The dashboard has `make seed-sandbox`, `make sync`, and `make record-payment ...` commands for the controlled test cycle.

### PayPal India KYC

PayPal India lists identity confirmation for the business type, PAN, an Indian bank account, and a purpose code for international payments; IEC may apply when exporting goods. Exact items depend on account and transaction type. You reported the PayPal setup/KYC as complete. None of this KYC is required for local or Sandbox app testing. See [PayPal India receiving payments](https://www.paypal.com/in/cshelp/article/how-do-i-receive-payments-through-paypal-help667).

## Render

- [x] Render account setup: done per your note.
- [x] Code pushed to private GitHub repository `ayshthkr/paypal-ai-hack` on branch `codex/late-payer-v1`.
- [x] GitHub sign-in completed; Render's GitHub App is restricted to the single private `ayshthkr/paypal-ai-hack` repository.
- [ ] In the open Render Account Settings page, choose **Git Deployment Credentials → Add credential → GitHub** and finish the authorization. GitHub sign-in is done, but Render still has no deployment credential; the GitHub App installation is already restricted to this one repository.
- [ ] Deploy the repository Blueprint from `render.yaml`.
- [ ] In the API service, set a generated `SESSION_SECRET`, operator username/password, corrected Sandbox credentials, and the recipient allowlist. Keep `PAYPAL_MODE=sandbox` for acceptance.
- [ ] Set the API service's `WEB_ORIGIN` to the deployed web URL and `PAYPAL_WEBHOOK_ID` after registering the webhook.
- [ ] Confirm the API `/ready` endpoint and complete the browser journey: demo invoice → forecast → edit reminder → operator login → approve/send → Sandbox buyer payment → refreshed status.

## AG Grid

- [x] Free option selected: AG Grid Community (MIT). It needs no license key or paid AG Studio account. The survival curve is a custom SVG component in the app.

## Not configured in this workspace

- [ ] Render Git deployment credential, service deployment, and hosted environment values are not configured yet.
- [ ] PayPal app-level Invoicing permission, webhook registration, and a Sandbox delivery remain to be completed.
