# Late Payer walkthrough

Use the [Guided demo tour](https://latepayer-web.onrender.com/tour) for a five-stop introduction. The tour keeps the selected stop in the URL and browser session, supports previous/next navigation, retries named story requests, and links to the packaged fallback if the API is unavailable. Tour controls remain available on destination screens so you can return to the selected stop, continue, or exit.

## Five-stop route

1. **Portfolio** — Lead with open cash exposure, overdue invoices, probability-weighted expected cash, and today’s review actions. Expected cash allocates each balance across calendar weeks using settlement probability mass. The gross-balance horizon includes disputed or unforecastable invoices so the open book reconciles.
2. **Verdant Market outlier** — Open the named synthetic invoice. Its unpaid age is a lower bound while it remains unpaid, not a payment-date prediction.
3. **Action queue** — Review a suggested action, its forecast reason, the internal note, recorded approval or skip outcome, and observed post-reminder payments. For an eligible synthetic invoice, choose **Generate AI draft** and confirm the displayed provider, model, and grounding facts. PayPal sends its own system-generated reminder body; the editable text is an internal review note.
4. **Model performance** — Show the release model, validation-based candidate selection, and the complete deployed scoring-path evaluation from generated artifacts. Expand the detailed tables for splits, calibration, and comparators. Synthetic metrics are pipeline evidence, not real-world accuracy claims.
5. **Reply triage** — Paste a synthetic client reply to preview its classification source and extracted date. Correct the preview, then approve or dismiss it. Invoice state changes only after approval; an approved dispute can later be resolved.

## Longer portfolio walkthrough

1. Use an aging bucket or expected-cash week to filter invoices.
2. Open a client and compare newly issued NET 15, NET 30, and partial-payment scenarios. The simulator scores the hypothetical invoice dates and terms. These are model associations, not causal estimates; displayed P50 dates represent full settlement, so partial payments can occur earlier.
3. Open an invoice to inspect its conditional forecast, source model/version, payment timeline, and reminder review. Choose **Generate AI draft** to request a draft grounded in the listed invoice and client-history facts. A successful draft identifies Google Gemini and its model; if generation is unavailable or fails validation, the template fallback is labeled. Review the facts and text before editing. Edited text remains an internal note and does not replace PayPal’s native recipient-facing email.
4. In the action queue, save an owner, notes, and a follow-up date for an old balance, then record its resolution or next step. Queue review duration and observed payment outcomes are measured separately; there is no manual-time baseline and no causal reminder-uplift claim.
5. Visit Model performance for generated release facts and the synthetic-data limits.

Currencies remain separate in the forecast. Client timing alerts distinguish habitual lateness from new worsening and require recent deterioration or a strong overdue outlier before creating a review signal.

## Isolated synthetic demo

The public app creates an isolated synthetic invoice snapshot for each browser session. Simulated approvals, replies, and payments affect only that session. Reset rotates the current browser session without changing another reviewer’s data or the shared base snapshot. If the API is unavailable, open [`/offline-demo`](https://latepayer-web.onrender.com/offline-demo) for the packaged, read-only snapshot, five navigation stops, and generated invoice, queue, model, and reply-triage examples. The 10-minute GitHub Actions health workflow warms both Render services and checks the portfolio, action queue, brief, stories, evaluation, invoice detail, and AI configuration; the free services can still have cold-start delays.

## PayPal Sandbox round trip

Use only the Sandbox merchant and recipients in `PAYPAL_ALLOWED_RECIPIENT_EMAILS`.

1. Sign in at [`/login`](https://latepayer-web.onrender.com/login), then expand **PayPal Sandbox** on the portfolio.
2. Choose an allowlisted Sandbox buyer, enter a small amount, select terms, and create/send the invoice. Open its detail and copy the hosted PayPal invoice link.
3. Sign in to the corresponding Sandbox buyer account and complete checkout on PayPal’s hosted invoice page. No real payment is made.
4. Confirm the `INVOICING.INVOICE.PAID` webhook reaches `/webhooks/paypal` and the invoice timeline records provider events. Use **Sync PayPal** if the callback is delayed.
5. Return to invoice detail and verify `PAID`, the paid amount, and zero outstanding balance. The portfolio totals then reflect the reconciled provider state.

PayPal’s native reminder controls the recipient-facing email body. The editable text is stored as an internal review note. Keep credentials in ignored environment files or Render settings; never add them to this walkthrough.

For local setup, see [README.md](README.md). Current Sandbox and deployment evidence is in [docs/execution-status.md](docs/execution-status.md).
