# Late Payer walkthrough

Use the [Guided demo tour](https://latepayer-web.onrender.com/tour) for a five-stop introduction. The tour keeps its current stop in the URL and browser session, supports previous/next navigation, retries named story requests, and links to the packaged fallback if the API is unavailable.

## Five-stop route

1. **Portfolio** — Lead with open cash exposure, past-due invoices, probability-weighted expected cash, and the actions ready for review. Expected cash allocates each balance across calendar weeks using settlement probability mass. The separate gross-balance horizon includes disputed or unforecastable invoices.
2. **Verdant Market outlier** — Open the named synthetic invoice. Its unpaid age is a lower bound, not a payment-date prediction.
3. **Action queue** — Review the internal collection note, captured approval/skip outcome, observed post-reminder payments, and the collections lane's owner, notes, follow-up date, and resolution status. The note is not the recipient email: PayPal sends its own system-generated reminder body.
4. **Model performance** — The page names the selected release model from generated artifacts, reports validation-based candidate selection including Cox, and provides an evaluation of the deployed conditional scoring path. Expand detailed tables for splits, calibration, and comparators. Synthetic metrics are pipeline evidence, not real-world accuracy.
5. **Reply triage** — Paste a synthetic reply to see its classification source and extracted date. Correct the preview, then approve or dismiss it. Invoice state changes only after approval; a dispute can later be resolved.

## Longer portfolio walkthrough

1. Use an aging bucket or expected-cash week to filter invoices.
2. Open a client and compare newly issued NET 15/NET 30 and partial-payment scenarios.
3. Open an invoice to inspect its conditional forecast, source model/version, payment timeline, reminder note, and reply-triage workflow.
4. In the action queue, save an owner and follow-up date for an old balance, then resolve it or set the next step. Queue review duration and recorded payment outcomes are measured separately; there is no manual-time baseline, so the app does not claim minutes saved or causal reminder uplift.
5. Visit Model performance for generated release facts and the synthetic-data limits.

Currencies remain separate in the forecast. Client timing alerts exclude habitual lateness and require a recent delay at least seven days worse than the client's usual median, a sustained worsening slope, a strong overdue outlier, or a gone-quiet cadence threshold.

## Isolated synthetic demo

The public app creates an isolated synthetic invoice snapshot for each browser session. Simulated approvals, replies, and payments affect only that session. Reset rotates the current browser session; it does not reset another reviewer's data or the shared base snapshot. If the API is unavailable, open [`/offline-demo`](https://latepayer-web.onrender.com/offline-demo) for the packaged, read-only snapshot and five navigation stops.

## PayPal Sandbox round trip

This walkthrough uses only the Sandbox merchant and recipients in `PAYPAL_ALLOWED_RECIPIENT_EMAILS`.

1. Sign in at [`/login`](https://latepayer-web.onrender.com/login), then expand **PayPal Sandbox** on the portfolio.
2. Choose an allowlisted Sandbox buyer, enter a small amount, select terms, and create/send the invoice. Open its invoice detail and copy the hosted PayPal invoice link.
3. Sign in to the corresponding Sandbox buyer account and complete checkout on PayPal's hosted invoice page. No real payment is made.
4. Confirm the subscribed `INVOICING.INVOICE.PAID` webhook reaches `/webhooks/paypal`; the invoice timeline records provider events. Use **Sync PayPal** if the callback is delayed.
5. Return to invoice detail and verify `PAID`, the paid amount, and zero outstanding balance. The portfolio totals then reflect the reconciled provider state.

PayPal's native reminder controls the recipient-facing email body. The editable text is stored as an internal review note. Keep credentials in ignored environment files or Render settings; never add them to this walkthrough.

For local setup, see [README.md](README.md). Current Sandbox and deployment evidence is in [docs/execution-status.md](docs/execution-status.md).
