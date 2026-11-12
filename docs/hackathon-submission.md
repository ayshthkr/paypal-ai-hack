# Late Payer — hackathon submission

## Project name

Late Payer

## Tagline

Know what may get paid, spot clients slowing down, and turn PayPal invoices into a human-approved collection workflow.

## Text description — ready to paste

Late Payer helps small businesses answer three urgent questions: What cash might arrive soon? Which customers are paying later than usual? What should I do next?

Built around PayPal Sandbox Invoicing, Late Payer connects invoice operations to payment forecasting and AI-assisted collections. Operators can create and send Sandbox invoices, import their status, review recommended reminders, and reconcile payments through verified PayPal webhooks or manual sync. PayPal is the transaction system behind the workflow: invoice state and outstanding balances are refreshed from the provider before reminder dispatch.

The dashboard turns an invoice list into a daily decision tool. A conditional survival forecast estimates time to full settlement, shows payment-date ranges and probabilities, and distributes expected receipts across calendar weeks. Client timing signals surface customers whose recent behavior has worsened or whose overdue invoice is an outlier. The action queue explains why an invoice needs attention and lets the operator edit an internal note, approve or skip an action, and track the outcome. Collections adds owners, follow-up dates, notes, and resolution status.

Google Gemini generates grounded reminder wording and classifies pasted customer replies into promises to pay, disputes, questions, or other messages. Transaction details come from application records; generated wording is validated and a labeled deterministic fallback handles unavailable AI. A person reviews and approves reply changes: a promise becomes a labeled forecast scenario, while an approved dispute pauses reminders. PayPal's native reminder endpoint controls the actual recipient email; edited draft text remains an internal review note.

AG Grid Community powers the interactive invoice workspace, including sorting, filters, configurable columns, and invoice/client navigation. Custom forecast charts and contribution tables connect portfolio totals to the invoices behind them. Render hosts the Next.js frontend, FastAPI backend, and Postgres database, making the working application accessible to judges.

The public demo requires no login and gives each browser an isolated synthetic portfolio. Judges can inspect forecasts, simulate approvals and payments, review replies, and reset their session without contacting PayPal. The authenticated operator route provides the separate, recipient-allowlisted Sandbox workflow. The model performance page exposes selection, calibration, held-out metrics, and limitations. Synthetic results demonstrate the pipeline; real-world prediction accuracy and collection uplift remain unproven.

## Tools / AI disclosure — ready to paste

| Tool or service | How we used it |
| --- | --- |
| **PayPal Developer Platform — Sandbox Invoicing v2** | OAuth-authenticated invoice creation/sending, invoice import/detail reads, human-approved native reminders, external-payment recording, and payment reconciliation. Verified, deduplicated webhooks trigger authoritative invoice refreshes. This is the central payment integration. |
| **Google Gemini API — `gemini-3.5-flash-lite`** | Server-side grounded reminder wording and bounded reply classification. JSON output validation, checked transaction facts, explicit human approval, and deterministic fallbacks keep the language model inside the review workflow. |
| **Survival modelling: LightGBM, Cox PH, Kaplan–Meier** | Train and compare payment-timing candidates with right-censoring and temporal evaluation; select by validation integrated Brier score. The checked-in release selects Cox PH. Produce conditional settlement probabilities, date ranges, and probability-weighted cash forecasts. |
| **AG Grid Community — sponsor tool** | Interactive invoice grid with custom presentation, sorting, filters, column preferences, and links to invoice/client evidence. The project uses the Community edition and custom SVG charts; AG Studio and Studio Agent Framework are not implemented. |
| **Render — sponsor tool** | Hosted frontend, API, and Postgres, configured in `render.yaml`; managed environment settings hold provider credentials. The project uses Render hosting rather than Render Workflows. |
| **Next.js / React / TypeScript / Bun** | Browser application, typed UI, server-side API proxy, and frontend build tooling. |
| **FastAPI / Python / SQLAlchemy / Postgres / uv** | API, persistent invoice/action/audit records, operator sessions, provider adapter, and model pipeline. |
| **Playwright / pytest / Ruff / ESLint** | Browser workflow verification, API/model tests, linting, and release checks. |

Suggested “Built with” tags: PayPal, Google Gemini, AG Grid, Render, Next.js, React, TypeScript, Python, FastAPI, PostgreSQL, LightGBM, Bun.

## Functional demo and source

- Working demo: <https://latepayer-web.onrender.com/>
- Guided tour: <https://latepayer-web.onrender.com/tour>
- Source repository: <https://github.com/ayshthkr/paypal-ai-hack>
- Local installation: [README](../README.md#local-setup).
- Presenter guide: [walkthrough](../walkthrough.md).
- Video: **PENDING — replace with public YouTube URL before submission.**

Judge test: open the portfolio, inspect an invoice, open the action queue, approve a simulated action, and review a reply preview before approving it. Public actions use synthetic session data. To test provider operations locally, configure your own PayPal Sandbox app and controlled buyer accounts using the README; hosted provider operations require operator access. Do not publish operator credentials or API keys.

## Requirement mapping

Checked against the [official rules](https://paypalaihackathon.devpost.com/rules) on November 12, 2026. The required combination is meaningful PayPal Sandbox integration plus AI; sponsor tools are optional.

| Requirement | Evidence / status |
| --- | --- |
| PayPal Sandbox plus AI | PayPal adapter in `services/api/app/services/paypal.py`; Gemini in `services/api/app/services/ai.py`; survival models in `ml/`. |
| Features and functionality description | Paste the text description above. |
| Identify AI tools and use | Paste the tools disclosure above. |
| Working build | Hosted app plus complete local setup. The packaged read-only fallback is supplemental evidence, not the functional build. |
| Public source and open-source license | Repository link above; root [MIT license](../LICENSE). Verify GitHub shows Public and MIT before submitting. Third-party dependencies retain their own licenses. |
| Public YouTube demonstration under three minutes | **Pending upload.** [Recording script](demo-video-script.md) supplied. |
| Continued judge access | Keep the demo available through December 15, 2026; check Render database expiry and the [hosting runbook](render-free-tier-runbook.md). |

Sponsor emphasis: **PayPal** powers the payment lifecycle; **AG Grid Community** makes invoice operations inspectable; **Render** serves the working full-stack demo. Gemini and survival modelling supply the AI layer. Only claim the integrations listed here.
