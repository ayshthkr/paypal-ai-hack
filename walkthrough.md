# Late Payer demo walkthrough

Updated 11 November 2026 after the speed, forecast, and workflow improvements. This screen-by-screen script describes what appears on each page, what to demonstrate, and how to explain the results.

The reset baseline for 11 November 2026 contains **52 synthetic clients, 2,129 invoices, 50 open balances, 30 past-due invoices, and 42 client timing signals**. These counts change as demo actions are applied. Forecasts describe full settlement, and client timing signals describe observed behavior; neither is a payment guarantee.

## Demo route

Start at the hosted [Late Payer portfolio](https://latepayer-web.onrender.com), then visit:

1. Invoice portfolio — `/`
2. Action queue — `/actions`
3. Client detail — open a client name from the portfolio or queue
4. Invoice detail — open an invoice number
5. Model performance — `/performance`
6. Operator sign-in and PayPal Sandbox — `/login`, then return to the portfolio

For a three-minute, five-stop route, use the [Guided demo tour](https://latepayer-web.onrender.com/tour): portfolio, Verdant Market outlier, action queue, model performance, and reply triage on the dispute story.

The hosted portfolio opens in **Synthetic demo** mode. Its invoices and forecasts are generated examples. Actions on those records are simulations and do not contact PayPal.

## A short live demo

Allow about eight minutes for the public flow; add the operator segment if you want to demonstrate Sandbox operations.

| Time | Screen | Demonstration |
| --- | --- | --- |
| 0–2 min | Portfolio | Explain the four summary cards; select an aging bucket and a settlement week; show that currencies stay separate. |
| 2–3 min | Action queue | Explain the ranking, edit a draft, refresh, then Skip and Undo skip. |
| 3–4 min | Client detail | Inspect settled payment delays, distinguish chronic lateness from a recent change, and compare terms. |
| 4–6 min | Invoice detail | Explain the conditional forecast, uncertainty, reminder history, and one synthetic payment. |
| 6–7 min | Model performance | Show the held-out results, calibration limits, subgroup errors, and model comparison. |
| 7–8 min | Portfolio | Move the demo clock back, show the historical banner, return to today, then confirm Reset demo. |

Before starting, open the portfolio and warm the API with [the minimal health check](https://latepayer-api.onrender.com/healthz). It confirms liveness only; the first portfolio request may still need time to wake the service and load data. Keep the clock at its latest date for action demonstrations. A skeleton or Retry state is not a forecast result. Do not reset during someone else’s demo because public synthetic state is shared.

## 1. Invoice portfolio

Open the hosted app. The first page is the receivables overview.

### What appears

- The top bar identifies **Late Payer** and links to **Model performance** and **Action queue**. **Operator login** opens the Sandbox sign-in page.
- The **Synthetic demo** banner explains that these are deterministic sample invoices and simulated actions never contact PayPal.
- **Ask your receivables** accepts a bounded question about invoice and client summaries. For example, ask, “Who owes me the most in EUR?” and select **Ask** to see the answer.
- **Morning brief** starts collapsed. Select **Show brief** to see expected cash this week, the highest-value candidates for review, and clients with timing signals.
- The **Demo clock** slider moves the portfolio date back by up to 60 days. Use **Reset demo**, then accept its confirmation, to restore the synthetic baseline and latest demo date. It clears simulated payments, reminders, replies, and review state while preserving Sandbox records. A rate limit prevents repeated resets in quick succession.
- When the clock is moved back, a **Viewing as of …** banner appears and synthetic mutations are blocked. Return to the latest date before approving actions or simulating payments.

### Read the portfolio summary

Four cards summarize the current portfolio: **Open invoices**, **Past due**, **Ready for review**, and **Client timing signals**. Select a card to filter the invoice list to that group.

The open-book headline leads with current balances and reconciled counts for open invoices, past due, and client timing signals. **Show USD equivalent** applies fixed demo rates (EUR 1.08, GBP 1.27, CAD 0.74) and labels them **FX not live**; the default view keeps source currencies separate. **If the top five late clients pay within 14 days** opens a week-by-week hypothetical cash scenario, clearly separate from the forecast.

The **Client payment timing** section gives examples of clients whose recent payment delays or invoice cadence have changed. Open a client name to inspect the underlying history. These are descriptive timing signals, not churn scores.

**Open invoice aging** groups balances into Not due, 1–30 days, 31–60 days, 61–89 days, and 90+ days. Day 90 belongs to the 90+ bucket everywhere. Select a bucket to filter the list; choose **Clear bucket** to remove that filter. Amounts remain separated by currency.

**Expected cash in · next 8 weeks** groups outstanding balances by the forecast p50 settlement week. Each week appears once, with separate amounts for each currency underneath. Select a week to filter invoices by their p50 settlement week and scroll to the grid. The aggregate accounts for the recovery estimate; it is not a cash guarantee. An invoice with an unresolved median may not appear in a displayed week.

### Explore invoices

The invoice grid includes invoice number, client, balance, due date, forecasted payment timing, client drift, and next action. Depending on the selected columns, it can also show status, the p10–p90 date range, probability of payment within 14 days, and expected cash gain.

- Select an invoice number to open its detail page.
- Select a client name to open that client’s detail page.
- Search for a client, invoice, or status. Focus **Search clients** to load matching clients; use the arrow keys, Enter, and Escape to operate the suggestions.
- Use the status multi-select and **Due from** / **Due to** fields. Sorting includes predicted settlement date and client drift as well as the other displayed choices. The Action queue sort reflects the displayed grid order; the expected-cash option is labeled **Assumed 14-day uplift · within currency**.
- Filters, sort order, page, and page size are kept in the URL. Reload or copy the URL to return to the same view. **All statuses**, **Clear bucket**, and **Clear settlement week** remove their respective filters.
- **Columns** controls which fields appear. **Export all filtered rows** downloads every matching row across pages, with p10/p50/p90 forecast columns. Closed invoices have blank forecast fields rather than a new future prediction.
- The **Open**, **Due this week**, **Overdue**, **Paid**, and **All** tabs change the invoice view. The pager and row-count selector move through larger result sets.
- The footer explicitly reports **All pages · not converted** open balance totals by currency for the current filtered view. It does not add USD, EUR, and GBP together.
- Use Tab to enter the grid and arrow keys to navigate cells. Invoice and client links are keyboard accessible. Hovering a link prefetches its data; paging retains the previous results while the next page loads.

Forecast confidence fades after 60 overdue days, with widening uncertainty bands. Balances at least 90 days overdue enter a separate human **Collections review** lane on the action queue and may show a labeled heuristic recovery estimate. Neither is a promise that collection will occur.

The **Forecast** label names the active model and its training cutoff. Forecast dates and probabilities are estimates derived from generated history; they are not promises about payment.

**Demo narration:** “This page helps me scan the portfolio: where the open balances are, which invoices need review, and which clients have a change in payment timing. I can move from a summary to the client or invoice evidence behind it.”

## 2. Action queue

Select **Action queue** in the top bar, or open [`/actions`](https://latepayer-web.onrender.com/actions). This is the daily review list, ranked within separate currency lanes under one shared five-action cap.

Each card shows the invoice and client, outstanding balance, due date, **Assumed 14-day cash uplift**, and a **why this action** sentence combining forecast, client timing, and policy. The uplift uses each invoice’s forecast with an assumed 10% reminder-effect scenario; it is not a causal estimate or measured outcome. Amounts are ranked within their currency, not compared across currencies. A separate **Collections review** lane lists 90+ day balances, the lane total by currency, and suggested human review steps; it never sends a reminder. **Refresh queue** reloads the recommendations. The line under the title shows the date, **pending** and **skipped** counts, daily cap, and whether the current actions are synthetic or use PayPal Sandbox.

For a reminder, read and edit the **Reminder draft**, then choose **Approve & send** or **Skip**. **Open invoice** takes you to the invoice detail page. A partial-payment suggestion instead explains that approval changes the invoice’s partial-payment setting; it does not send a reminder email.

In Synthetic demo mode, approval records a simulated action. In the operator workflow, an approval sends a reminder through PayPal Sandbox after a confirmation prompt. Sandbox actions are test operations for the controlled recipient.

To demonstrate review without dispatching a reminder:

1. Edit an eligible reminder’s **Reminder draft**. Pause briefly so its background save can finish, then choose **Refresh queue** to show that the edit persists.
2. Choose **Skip** and point out the skipped count.
3. Select **Undo skip · …** to restore the unchanged recommendation. Skipping is a review decision and does not count as a reminder sent.

After a simulated or Sandbox reminder is sent, the queue shows its next-week outcome as **paid**, **partial**, **silent**, or **checking** until the seven-day observation window closes. This is a temporal association, not proof the reminder caused payment. **Approve all friendly** opens a confirmation and applies only to friendly reminders in the displayed queue. It can dispatch Sandbox notifications when signed in; inspect the source before confirming. After an approval or skip, keyboard focus moves to another available action or the refresh control.

Reminder tone and cooldown use recorded reminder history. A prior friendly and firm reminder must not be narrated as a new first contact. Some invoices intentionally have no action because of a promise, dispute, cooldown, holdback, or the daily cap.

If no cards appear, the page explains that no reminder or partial-payment action is pending for the current date.

**Demo narration:** “The queue turns the portfolio into a short list for a person to review. The recommendation includes its reason and editable text, and I choose whether to approve or skip it.”

## 3. Client detail

Open any client name from the portfolio, queue, or invoice detail. This page brings together the client’s payment history and current open invoices.

### Summary and payment history

The heading shows the client name, whether the record is **Synthetic** or **PayPal Sandbox**, invoice counts, and the current timing signal. The summary cards show:

- **Settled invoices** and the median number of days late, when available.
- **Partial-payment history** based on invoices with multiple payment records.
- **Current open balance**, separated by currency.
- **Activity signal** and the evidence text behind it.

The **Payment delay history** chart places each fully settled invoice relative to its due date. Bars above the baseline indicate late payment; blue bars below it indicate on-time or early payment. It labels the visible slice as the **last 24 of 49 settlements** when that client has the full default history. A **habitually late** label distinguishes consistently late payment from a recent deterioration: stable behavior can still be chronically late. Open overdue invoices contribute right-censored lower bounds to the timing signal. With fewer than five settlements, **limited history** identifies the use of pooled priors instead of implying a strong individual estimate.

### Compare terms scenarios

In **What if the next invoice had different terms?**, choose **NET 15** or **NET 30**, then select **Compare scenarios**. The results compare:

1. A NET 30 invoice without partial payments.
2. The selected term without partial payments.
3. The selected term with partial payments allowed.

For each scenario, compare the estimated median full-settlement date and the probability of payment within 14 days. The page identifies when it has limited client history. This is a model comparison for a representative invoice amount, not a change to an existing invoice.

### Client invoices

The invoice table lists issue and due dates, status, outstanding amount, payment probability, and next action. Use its status filter and Previous / Next controls to page through the client’s invoices; these choices remain in the URL. The portfolio link opens a view filtered to this client. Select an invoice number to continue to its detail page.

**Demo narration:** “Here I can see whether the timing signal is supported by actual settled invoices, then compare how different terms or allowing partial payments would change the forecast for a representative invoice.”

## 4. Invoice detail

Open an invoice number from the portfolio, action queue, or client invoice table. The page identifies the invoice source, number, client, and status. When opened from a filtered portfolio, the **previous** and **next** controls move through that same view.

### Payment timing forecast

The **Payment timing forecast** section shows the estimated median full-settlement date and a survival curve. The horizontal axis is time from the scoring date; the curve represents the probability that the remaining balance is still unpaid. The p10, p50, and p90 dates and payment probabilities all come from that curve. A separate, wider **validation-calibrated coverage interval** reports its method and paid-only limitation. The cards below show the chance of payment by the due date, within 14 days, and within 30 days.

The reason text explains facts used for the estimate. The footer names the model version, scoring time, and 180-day horizon. A date beyond that horizon means the forecast did not reach that quantile within the displayed period; it does not mean the invoice will never be paid. Forecasts describe full settlement, even when the balance may be paid in parts.

For an invoice that is already overdue, the curve conditions on the fact that it is still unpaid at its current age. Uncertainty widens progressively after 60 days overdue and the interface marks that lower confidence. The 90+ day recovery estimate is a shrinkage heuristic, not an established probability of eventual collection. **Limited history** appears when fewer than five prior settlements support the forecast.

For paid invoices, **Paid outcome** compares the observed payment date with the stored issue-time forecast, when that information is available.

### Client timing, invoice facts, and timeline

**Client timing signal** explains the current signal and the settled history behind it. The note distinguishes the history available to the invoice forecast at its scoring date from the history used by the current client signal.

The **Invoice status** panel shows outstanding and paid amounts, due date, currency, recipient view information, partial-payment setting, payment terms, and the current decision with its policy reason. A PayPal Sandbox invoice can also show **Open PayPal invoice** and **Sync PayPal**.

The **Invoice timeline** lists dated events such as issue, delivery, views, reminders, payments, and provider events. The recipient-view status and timeline derive from the same observed view timestamp. Due and issue dates remain calendar dates; actual event timestamps display consistently in **Asia/Kolkata (IST)**. The action history for a reminder appears in the review panel on the right.

Choose **Copy invoice link** to copy the current URL. A link to an unknown invoice shows **Invoice not found** with a route back to the portfolio; it does not continuously retry a 404.

### Review an action or try a synthetic update

The review panel is labeled **Reminder review** or **Partial-payment review**, depending on the proposed action.

- For a reminder, read and edit the preview. Move focus out of the editor to save the nonempty draft to the API; reload to demonstrate persistence. Drafts are scoped to the recommendation. **Generate AI draft** requests another draft when available. **Approve & simulate** records a synthetic action; **Approve & send** submits an approved Sandbox reminder after confirmation. **Skip** records that the proposal was skipped.
- For a partial-payment proposal, review the setting and choose the approval button. This changes whether partial payments are allowed; it is separate from sending a reminder.
- **Grounded in:** identifies the invoice facts supporting the draft, and **Draft source** distinguishes Gemini from the grounded template. AI draft and answer requests retain their input facts in an audit record. Draft generation falls back to a template if the optional model is unavailable or misses its three-second deadline.
- The delivery note explains what the action does. In Sandbox mode, PayPal controls its native reminder email body; the approved text is kept in the app’s audit record.
- **Client reply triage** lets you paste a reply such as “We will pay Friday.” Choose **Classify reply** to record the classification and any promise or dispute information. A dispute pauses reminders. An active promise date adds an explicitly labeled 25% illustrative scenario to the forecast curve; promise adherence is not learned from training data and the scenario is not a guarantee.
- **Action history** shows attempts and their current state. If a provider action is marked uncertain, reconcile the invoice with **Sync PayPal** before retrying.

For a synthetic invoice with a remaining balance, **Simulate partial payment** reduces the balance, while **Simulate full payment** marks it paid. Both are demo-only controls and do not contact PayPal. After a partial payment, inspect the lower outstanding balance and refreshed forecast; after a full payment, inspect the paid status. Return to the portfolio to see the changed counts. Restore the baseline with **Reset demo** after finishing this segment. Historical views block mutations until you return the clock to its latest date.

**Demo narration:** “The invoice page combines the forecast with the facts behind it, the client context, and the action controls. I can review the proposed message, see the audit trail, and distinguish a simulated demo action from a Sandbox operation.”

## 5. Model performance

Select **Model performance** in the portfolio header, or open [`/performance`](https://latepayer-web.onrender.com/performance). The first panel leads with the calibrated interval result and shows the held-out cohort size, including right-censored open invoices used by survival metrics.

At the regenerated 11 November 2026 run, **306 paid invoices** had a **16.5-day median absolute P50 date error** and the validation-calibrated interval covered **81.0%** against its **80% target**. Raw curve P10–P90 coverage was **77.5%**. The test cohort had **356 invoices**, including **50 right-censored invoices**. Paid-only date error and interval coverage are descriptive and do not include the censored invoices; Uno concordance, time-dependent AUC, integrated Brier, and 30-day IPCW calibration retain appropriate censoring information.

- The **Model card** names pooled Kaplan–Meier as the deployed forecast: it was selected because validation IPCW integrated Brier was **0.076**, compared with **0.084** for LightGBM. On the held-out split, the selected model beats LightGBM on integrated Brier but loses on Uno concordance and due-date AUC; the Cox comparator has stronger held-out concordance and due-date AUC. Client-bootstrap intervals and known limits are displayed next to that choice.
- **Paid within 30 days · IPCW calibration** bins predicted payment probabilities against censoring-weighted outcomes. This run had **328 evaluable invoices**, including **22 open invoices known to remain unpaid at day 30**; **28** earlier-censored invoices are excluded from these binary bins but remain in survival metrics.
- **Calibration-to-test residual drift** compares signed paid-invoice P50 residuals with a two-sample KS test. This run had calibration n=442, test n=306, and KS p=0.745, so recent-window calibration was not selected. It is diagnostic, not a censoring-adjusted guarantee. **Coverage by issue month** makes time variation visible.
- **Feature ablations** report concordance and integrated Brier for no weekday (already absent from the v6 schema), no reminder features, and client-history-only models. The no-weekday row is the full LightGBM feature set; the other rows are retrained variants.
- **Client drift versus simple baselines** compares the production detector with recency-only ranking and the rule “median delay of the last three settled invoices > 1.5× the preceding median.” The labels are generated by the synthetic data rules, not real-world outcomes.
- The assumed reminder-effect table spans **5%, 10%, and 20%** across **3, 5, and 7 days advanced**. It is a sensitivity analysis, not an estimated causal benefit.
- The independent [public dataset evaluation](docs/public-dataset-evaluation.md) reports a temporal Cox/Kaplan–Meier check on a historical, non-commercial Kaggle cohort; it is not a result for the deployed synthetic-trained model.
- **Predicted vs actual pay dates**, error by currency and overdue age, LightGBM comparator feature importance, LightGBM SHAP, and evaluation split remain available below the summary. SHAP describes model sensitivity, not causal effects.

The drift backtest flagged **35 clients** and found **12 true positives among 15 generator-defined positives** (**34.3% precision, 80.0% recall**). The recency-only baseline had **20.0% precision and 46.7% recall**. The simple last-three-delay rule flagged **8 clients**, with **4 true positives** (**50.0% precision, 26.7% recall**). These comparisons do not establish real-world performance.

**Demo narration:** “The release uses the model that won the preselected validation Brier criterion. On this synthetic held-out cohort it gains calibration over LightGBM but loses on rank discrimination. Coverage is a little below target, and I can show the censoring, residual drift, monthly slices, and ablations behind that result.”

These results inspect the synthetic pipeline; they do not establish production accuracy.

## 6. Operator sign-in and PayPal Sandbox

Choose **Operator login** from the portfolio or open [`/login`](https://latepayer-web.onrender.com/login). The page has **Username** and **Password** fields and a **Sign in** button. After successful sign-in, it returns to the portfolio and shows **Operator session** in the header.

The operator portfolio includes a **PayPal Sandbox** panel. It reports the latest sync and webhook retry status, and lets the operator:

1. Select an allowlisted Sandbox recipient.
2. Enter an amount and choose due-on-receipt, NET 15, or NET 30 terms.
3. Optionally allow partial payment.
4. Select **Create & send Sandbox invoice**.

The new invoice appears in the portfolio and can be opened like any other invoice. Use **Sync PayPal** to import and reconcile provider state. On an invoice page, the operator can review an eligible reminder and confirm **Approve & send**. The app checks the invoice state and recipient allowlist before dispatch.

All PayPal operations in this walkthrough are restricted to Sandbox and the configured recipient allowlist. Sandbox invoices can send test notifications to that recipient. They do not move real money. The hosted operator credentials are stored in the ignored `.env.render` file; do not copy them into this document or source control.

## Run the demo locally

The local synthetic setup does not require PayPal credentials. From the project directory, with Bun 1.3.6, Python 3.12 or 3.13 and `uv`, and Docker Compose installed:

```sh
test -f .env || cp .env.example .env
# Set non-default OPERATOR_USERNAME, OPERATOR_PASSWORD, and SESSION_SECRET in .env.
make setup
make dev
```

Open [http://localhost:3000](http://localhost:3000) for the app. The current seed contains 52 synthetic clients and 2,129 invoices, with 50 open and 30 past-due balances at its 11 November 2026 baseline; the portfolio shows 42 descriptive client timing signals. `AS_OF` defaults to today in the merchant timezone; the seed includes eight clients with behavior changes and three quiet clients. The API docs are at <http://localhost:8000/docs>; minimal liveness is at <http://localhost:8000/healthz>.

Useful demo commands:

| Command | What it does |
| --- | --- |
| `make seed-demo AS_OF=today` | Rebuilds the synthetic fixture, trains the model, and refreshes projections; use this to start a new local baseline. |
| `make train AS_OF=today` | Evaluates temporal cohorts, then refits the deployment artifact through today. |
| `make rescore-demo` | Refreshes stored demo forecasts/client signals and captures the reset baseline; run after seeding/training, before demo mutations. |
| `make check` | Runs Ruff, API tests, ESLint, TypeScript, and the production build. |
| `make smoke` | Checks seeded API reads against the two-second warm-read budget. |
| `make browser-smoke` | Runs Playwright against the running UI/API. |
| `make sync` | Imports Sandbox invoices and retries stored webhook refreshes. |
| `make seed-sandbox` | Creates and sends a few invoices to configured, allowlisted Sandbox recipients. |

Keep `PAYPAL_MODE=sandbox` for PayPal operations. Sandbox credentials and recipient settings belong in ignored environment files or hosting settings.

## Verification and rehearsal checklist

The post-speedup review implementation is on `codex/late-payer-v1` in commits `c8e6fd1` and `5f3ac6c`, pushed to `origin`. The checks completed on 11 November 2026 were:

- **64 API tests passed**, plus Ruff, ESLint, TypeScript, and the production build through `make check`.
- `make smoke` passed against the seeded local API. Two Playwright tests passed against the local production build, including synthetic draft edit/save/reload/restore.
- Render's `/healthz` liveness endpoint returned HTTP 200 with only `{"ok": true}`. It does not report database, Sandbox, operator, PayPal, webhook, or model readiness.
- Latest hosted warm samples: portfolio **0.680 s** (**22,065 bytes**), invoice detail **1.230 s**, client list **0.482 s**, client facts **1.740 s**, action queue **0.613 s**, evaluation **0.412 s**, and all-invoice page **0.511 s**. These samples met the two-second read target; invoice detail and client facts can exceed the one-second aspiration and timings vary with cache and instance state.
- The hosted root loaded in **0.408 s** with a 60-second shared cache policy. `/actions` loaded in **1.740 s**. HSTS, `X-Content-Type-Options`, and CSP were present on the root, app routes, and branded 404 response. Private API reads used `private, max-age=30, stale-while-revalidate=120`.
- An unauthenticated hosted check returned **401** for Sandbox sync, sync status, recipient listing, invoice creation, reminder send, and partial-payment approval. Public portfolio, invoice, queue, evaluation, and client responses contained no PayPal IDs, payment URLs, or PayPal links; the portfolio rows were synthetic demo records.
- The API had one cold wake that exceeded a 40-second readiness attempt; after the instance woke, the health checks and routes returned 200. Keep the warm-up workflow enabled before a judging window. These are point-in-time measurements, not latency guarantees.
- The public demo and local synthetic workflow were exercised. **No Sandbox invoice, reminder, buyer payment, or webhook round trip was sent during this deployment check.** A new operator password has been generated in the ignored `.env.render` file, but the hosted Render value still needs to be updated by the account owner before authenticated Sandbox checks. Do not copy the credential into this document or chat.

For a hosted rehearsal, run from the project directory:

```sh
SMOKE_BASE_URL=https://latepayer-web.onrender.com/api make smoke
PLAYWRIGHT_BASE_URL=https://latepayer-web.onrender.com make browser-smoke
```

Browser tests require Playwright’s Chromium browser to be installed. The optional draft test requires `E2E_EDITABLE_INVOICE` to identify an eligible, controlled synthetic fixture; leave it unset for a navigation-only hosted check.

Before presenting, check these visible outcomes:

1. The Synthetic demo banner is visible and the clock is at its latest date; the baseline is 50 open, 30 past due, and 42 timing signals.
2. Filters survive reload, week/bucket selection reaches the grid, and currency totals say not converted.
3. An edited reminder survives refresh; Skip and Undo skip work for an unchanged proposal.
4. An old overdue invoice shows uncertainty rather than a universal “tomorrow” forecast.
5. A historical view shows its banner and cannot mutate synthetic invoices.
6. The performance page names the pooled Kaplan–Meier release choice, shows 79.4% coverage against an 80% target, and includes censor-aware scores and an external public-data check.
7. After the demo, confirm Reset demo once and check that the baseline has 50 open balances.

Implementation details and measurement caveats are in [Task List 2 implementation](docs/task2-implementation.md). Credentials remain in ignored environment files or hosting settings; this walkthrough contains no login secrets.
