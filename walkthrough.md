# Late Payer website walkthrough

Use this as a three-minute presenter script for the [hosted demo](https://latepayer-web.onrender.com/). The site labels its public data **Synthetic demo** and says: “Deterministic sample invoices. Simulated actions never contact PayPal.”

## Start here

Open the home page and point out the headline, **Invoice portfolio**, and its line: “See what may get paid, who is slowing down, and what needs a human decision.” The header links to **Guided tour**, **Demo stories**, **Model performance**, and **Action queue**. Select **Guided tour** for the timed route.

The tour is labeled “THREE-MINUTE ROUTE · FIVE STOPS.” Each stop has a short talk track, an explanation, and a clearly labeled live or local-fallback destination. Use the tour page’s single set of stop controls to choose or continue the story; destination pages keep a compact previous/continue/exit strip. Every product page also has a direct **Read-only fallback** link.

## Five-stop talk track

### 1. Portfolio

The tour’s prompt is: “Open balance, past-due invoices, and current client timing signals.” Open the portfolio and show the open balance, overdue work, client timing signals, and probability-weighted cash by week. The weekly view spreads expected receipts across the forecast distribution instead of assigning every balance to one median date.

### 2. Verdant Market outlier

The tour’s prompt is: “An unpaid invoice’s overdue age is a lower bound, not a predicted payment date.” Open **Verdant Market outlier** and explain that an invoice still unpaid at the snapshot date has not settled by then; that does not establish when it will settle. Review the conditional forecast, the client’s right-censored history, and the policy reason together. The tour bounds the live story lookup to 20 seconds. If it fails, the destination is labeled **Open local fallback**; the packaged invoice evidence remains expandable without the API.

### 3. Action queue

The tour’s prompt is: “Every suggestion has a forecast reason and a human approval step.” Open **Action queue**. The page describes “suggested reminders and partial-payment changes,” explains why policy thresholds change reminder severity, and shows how the shared five-action cap rotates across currency lanes. Review the internal note, recipient-facing PayPal reminder boundary, human approval, and recorded outcome. The separate **Collections** route filters owner, follow-up, and resolution work. Synthetic approvals are simulations; they do not send reminders or change PayPal records.

### 4. Model performance

The tour’s prompt is: “The release model is named against its validation selection rule and held-out results.” When **Model performance** loads, show the model selection, censor-aware evaluation, and calibration. The tour cautions: “Synthetic results are pipeline evidence, not real-world accuracy claims.” Keep that distinction clear when discussing the reported scores.

### 5. Reply triage

The tour’s prompt is: “A reviewed promise changes the forecast as a labeled scenario; an approved dispute pauses reminders.” Show how a reply is previewed, corrected, and then explicitly approved. A preview alone does not change invoice state; approval is the decision point.

## If the live API is slow

Portfolio, queue, story, and performance details load from the API. Errors retain the last successful result when one exists and provide a task-specific retry; use the visible **Read-only fallback** link directly when you need to finish the route without the API. Avoid quoting live metrics until the performance page loads. Open the [offline demo](https://latepayer-web.onrender.com/offline-demo?stop=2) for the packaged versioned snapshot.

The fallback identifies its package version and snapshot date. The current packaged snapshot is **late-payer-fallback-v2**, dated **2026-11-11**, matching the checked-in model evaluation: **50 open invoices · 30 past due · 1 disputed**. Currency totals remain separate. For a concrete row, show Verdant Market invoice **LP-16-045**, **USD 4,893.00**, due **2026-07-09**, marked **OVERDUE**. Expand its packaged evidence to show issue/due dates, total, paid and remaining balance, terms, partial receipts, and reminder dates. The disputed example is Cinder & Co invoice **LP-06-051**, **GBP 7,539.00**. The page explains that it still needs the web app and packaged files to load, so airplane-mode availability is not guaranteed.

Use the fallback to explain its read-only invoice evidence and navigation. It does not present the API as available and does not mutate shared data. Return to the live portfolio with **Retry live portfolio** when the API is available.

## Optional deeper tour

- Open an invoice to review its forecast, payment timeline, and reminder decision. Explain that the P50 date represents full settlement; partial payments may arrive earlier.
- Open a client’s terms scenarios to compare the hypothetical dates and terms. Describe these as model associations, not causal effects.
- In the action queue, show the owner, notes, follow-up date, and resolution fields used to organize collection work.
- Use **Model performance** for the release artifact, held-out results, and synthetic-data limitations.

## PayPal boundary

The public tour uses synthetic invoices and simulated actions. For an operator walkthrough of provider features, sign in through [Operator login](https://latepayer-web.onrender.com/login), then open the dedicated [PayPal operator route](https://latepayer-web.onrender.com/operator). Keep PayPal in **Sandbox** mode with the controlled recipient allowlist. The checklist walks through create → pay → webhook or sync → balance confirmation and shows timestamped evidence. PayPal’s native reminder controls the recipient-facing message; edited copy is an internal review note.

### Optional Sandbox invoice round trip

1. Sign in as the operator, then open **PayPal operator** from the navigation.
2. Choose an allowlisted Sandbox buyer, enter a small amount, select terms, and create/send the invoice. Open its detail and copy the hosted PayPal invoice link.
3. Complete checkout using the matching Sandbox buyer account. This is a Sandbox payment, not a live payment.
4. Confirm the paid event or use **Sync PayPal** if the callback is delayed. Verify the invoice shows `PAID` and zero outstanding balance.

Use only the controlled recipient allowlist. See [execution status](docs/execution-status.md) for the verified Sandbox and deployment details.
