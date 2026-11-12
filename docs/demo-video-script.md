# Late Payer — 2 minute 45 second demo script

YouTube URL: **PENDING UPLOAD**

Record the functioning hosted app. Warm it before recording. Use public synthetic actions for the interactive segment; keep any operator credentials out of the capture. If showing the provider round trip, use the existing Sandbox evidence or record a controlled Sandbox run separately.

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:20 | Portfolio overview | “Small businesses see overdue invoices, but still need to decide what cash may arrive and who needs attention. Late Payer combines PayPal invoicing, payment forecasts, and AI-assisted collections in one working dashboard.” |
| 0:20–0:45 | Cash forecast, then invoice contributors | “Expected cash is distributed across weeks using settlement probabilities. AG Grid lets us sort and filter the invoice book, then inspect the records behind a total. Currency amounts stay separate.” |
| 0:45–1:10 | A client timing signal and invoice detail | “This customer is paying later than usual. Opening the invoice shows a conditional forecast and payment-date range. An unpaid overdue invoice is treated as censored history, so its current age is not mistaken for its payment date.” |
| 1:10–1:40 | Action queue; open review and approve a simulation | “The queue explains the next suggested action. Gemini assists with grounded wording, but a person reviews the facts and approves. This public action is a simulation. In the operator workflow, the approval invokes PayPal's native Sandbox reminder; the editable wording is retained as an internal note.” |
| 1:40–2:05 | Invoice reply triage; preview, correct, approve | “Gemini classifies a customer reply. A preview changes nothing until approval. A reviewed promise creates a labeled payment scenario; an approved dispute pauses reminders.” |
| 2:05–2:25 | Operator checklist or captured Sandbox evidence | “The PayPal integration creates and sends Sandbox invoices, imports status, and reconciles payment through verified webhooks or sync. Provider operations are authenticated and restricted to controlled recipients.” |
| 2:25–2:45 | Model performance; finish on portfolio and links | “The forecast compares survival models and reports held-out evaluation. These synthetic results validate the pipeline, not real-world accuracy. Render hosts the working frontend, API, and database. Try the demo or run the MIT-licensed source yourself.” |

Before upload: confirm the final cut is under three minutes, shows actual interaction, contains no credentials, and matches the features demonstrated. Upload publicly to YouTube and replace the pending URL in the submission pack. The [official rules](https://paypalaihackathon.devpost.com/rules) specify the video requirement.
