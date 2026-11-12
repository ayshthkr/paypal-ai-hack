# Final walkthrough audit — 12 November 2026

> Historical review snapshot, captured before the later reviewer-workflow fixes and Carbon UI changes. Findings below are preserved as recorded; consult `handoff.md` for subsequent implementation and verification. This is not a current unresolved-defect list.

Reviewed the updated walkthrough, README, handoff, application behavior, and relevant implementation. Used the ChatGPT in-app Browser for the live portfolio, queue, invoice and client details, terms comparison, performance, guided tour, fallback, and operator login surface. Operator-only PayPal workflows were source/document inspected; no provider invoice, reminder, or payment was executed.

The product forecasts full settlement using a conditional survival distribution, aggregates probability-weighted receipts, detects changes in client payment behavior, and proposes policy-based actions with human approval. Synthetic mutations are isolated simulations. Gemini drafts notes; the survival model, policy, and structured question handlers provide other decisions. Terms and uplift are hypothetical associations/scenarios, not measured causal effects.

## What worked in this run

- The initial live portfolio, queue, and model report loaded. Ask and the morning brief agreed on this week's EUR 559.41, GBP 2,246.32, and USD 1,831.55 forecasts.
- Gemini generated a grounded reminder draft. Terms comparisons showed explicit noncausal and uncertainty limitations. Tour controls persisted on destination pages, and the fallback included the named examples.
- The historical-date request failed, but correctly rolled back to the last successful snapshot. Collections owner and notes survived a full reload, confirming the save persisted despite a subsequent refresh timeout.
- Existing fixes for weekly probability aggregation, conditional Brier calculations, headline finite-interval coverage, synthetic action labels, and scenario caveats should be retained.

## Interaction-revealed feature inventory

- **Ask and morning brief:** Expand “Ask questions and view the morning brief”; the brief needs a second “Show brief” click. Questions cover a small set of grounded portfolio intents.
- **Demo stories:** Open the header menu for Verdant's outlier, Cinder's dispute, Harborlight's firm reminder, and Blue Cedar's terms example.
- **Guided route:** Five stops link to actual screens; the destination bar preserves Previous, Continue tour, and Exit tour.
- **FX display:** The USD-equivalent toggle changes aggregate display using disclosed illustrative fixed rates; original invoice currencies remain available.
- **Summary drilldowns:** Portfolio views, aging buckets, client signals, action filters, and cash-week cards modify the invoice list.
- **Historical clock:** Moving and committing the slider requests a prior snapshot; failed requests restore the last successful date.
- **Late-client scenario:** The hypothetical top-five-client card exposes projected cash timing separate from the baseline forecast.
- **Client picker:** Focusing the client selector loads searchable choices; selecting one scopes the portfolio.
- **Advanced filters:** Status multiselect, due-date bounds, sort direction, settlement week, and pagination change the grid; some selections live in the URL.
- **Columns and horizontal scroll:** The Columns menu reveals status, P10–P90 range, and assumed uplift; more columns, including actions, are off-screen horizontally.
- **CSV export:** Export operates on the filtered result set, beyond the currently visible page.
- **Invoice navigation:** Invoice and client names open separate detail pages; invoice details include adjacent-invoice navigation and a copy-link action.
- **Forecast detail:** An invoice reveals conditional payment probabilities, quantiles, survival chart, model identity, policy reason, and event history.
- **Client history:** Client details reveal payment behavior and delay bars; hovering a bar exposes invoice, settlement date, and early/late delay.
- **Terms comparison:** Select terms and press Compare to reveal baseline and hypothetical settlement dates, including a partial-payment scenario.
- **Draft generation:** Eligible proposed reminders expose Generate AI draft, an editable internal note, provenance, and approval/skip controls. Draft editing saves on blur.
- **Reply triage:** Paste a reply, classify it, inspect/correct the preview, then approve or dismiss. Promise/dispute state changes happen at approval.
- **Dispute resolution:** A disputed invoice exposes a separate resolution action and suppresses normal forecasting/reminders while disputed.
- **Simulated payment:** Synthetic invoice controls expose full and half-balance payment simulations; these are separate from provider checkout.
- **Queue shortcuts:** Skip exposes Undo; “Approve all friendly” provides a bulk operation for the friendly subset.
- **Collections workflow:** Older debt reveals owner, follow-up date, resolution status, and notes with a separate Save action.
- **Performance detail:** Expanding detailed metrics reveals calibration, monthly coverage, ablations, drift comparisons, policy scenarios, public-data results, error slices, and comparator explanations.
- **Fallback route:** `/offline-demo` contains a packaged, read-only portfolio and named examples with stop navigation and a return-to-live link.
- **Operator-only provider tools:** Login reveals Sandbox creation, allowlisted buyers, terms/partial-payment controls, provider links, sync, and provider status. These were not transaction-tested in this audit.

## Ranked improvement backlog

`Observed` means reproduced in Browser; `Code` means supported by implementation inspection; `Design` means a proposed improvement rather than a broken feature. P1 comes before P2 and P3; ordering within each group reflects reviewer/demo impact. No P0 was established in this run.

### P1 — correctness and demo reliability

- **P1 · Observed + Code — Fix cash-week drilldown semantics.** The Nov 9 week displayed cash from 23 invoice contributions but opened an empty grid. Cards aggregate probability mass, while the filter selects P50 dates; show contributing invoices and their expected contribution.
- **P1 · Observed — Remove long waits from core paths.** A historical snapshot and the Verdant detail timed out, and reply approval timed out after roughly 30 seconds. Profile these paths and make the five-stop demo consistently finish within its promised time.
- **P1 · Observed + Code — Reconcile mutation outcomes after timeouts.** Collections saved successfully despite a failed refresh; promise approval remained pending after a timeout. Distinguish failed writes from failed refreshes and re-read authoritative state before inviting a retry.
- **P1 · Observed + Code — Make skip and Undo coherent across screens.** Skip Daybreak, open its invoice, then Undo returns “A replacement action already exists.” Prevent read-triggered replacement from invalidating Undo, or replace the stale control with a direct reconciliation action.
- **P1 · Code — Give simulated payments stable retry identities.** A fresh random payment identifier does not protect a repeated user request after an uncertain response. Reuse a request identity and reconcile the recorded payment before allowing a duplicate submission.
- **P1 · Observed + Code — Standardize request deadlines and recovery.** Some writes and their refreshes follow different timeout/error paths. Bound operations consistently, retain drafts, and show operation-specific Retry and Check status controls.
- **P1 · Observed + Code — Expose fallback before the demo stalls.** The homepage can spend many retries before recovery appears, while failing detail/queue/performance routes lack an equally direct packaged-demo exit. Put a visible fallback link on every live-dependent screen.
- **P1 · Observed + Code — Align metric denominators and labels.** Headline coverage excludes 12 unbounded intervals, while currency coverage uses a different denominator. Show eligible, excluded, and covered counts per slice, plus whether intervals are raw or calibrated.
- **P1 · Observed + Code — Remove unqualified “Expected gain” from the brief.** Queue uplift is correctly disclosed as a 10% assumption, but the brief presents bare expected-gain money. Use the same explicit assumed-scenario label wherever that amount appears.

### P2 — reviewer comprehension and complete workflows

- **P2 · Design — Make the end-to-end product story visible immediately.** Put forecast cash, changed clients, recommended action, human review, and outcome in one obvious sequence. The current long portfolio makes these capabilities feel disconnected.
- **P2 · Observed — Give Ask and the brief independent entry points.** The brief sits behind two disclosures and is easy to miss. Surface a compact morning summary with a clear expand action and a separate Ask control.
- **P2 · Observed + Code — Explain Ask's supported scope.** Offer the four supported question families as prompts and show the matched time window/currency. A broad text box currently implies more conversational capability than the structured handlers support.
- **P2 · Design — Make answers actionable.** Link Ask and brief results to the exact clients, invoices, or filtered cash view supporting them. Add the data date and relevant scope directly beside the answer.
- **P2 · Observed — Keep active filters visible and removable together.** Add filter chips and one Clear all action covering search, client, status, date, bucket, and forecast week. Separate reset buttons make combined filters difficult to diagnose.
- **P2 · Observed — Distinguish filtered emptiness from missing forecasts.** A nonsense search produced zero-invoice cohort copy and “Forecast unavailable,” while global client counts stayed nonzero. Say “No matching invoices” and identify which cards remain portfolio-wide.
- **P2 · Observed — Indicate freshness during filter requests.** Existing rows can remain visible while a new search/filter is pending. Show a local loading state and the applied-query scope so stale results are not mistaken for the new answer.
- **P2 · Observed — Define dashboard scopes consistently.** Label global versus filtered totals and reconcile open, overdue, disputed, action-eligible, and unforecastable counts. Keep explanatory denominator text beside the number it explains.
- **P2 · Observed — Explain weekly forecast counts.** Label parenthesized numbers as contributing invoices and explain that one invoice may contribute across several weeks. Show contribution amounts in the corrected drilldown.
- **P2 · Observed — Explain the nine buckets in an eight-week horizon.** Partial calendar weeks can create nine displayed week starts. Show explicit date bounds and mark the current partial week rather than implying nine full weeks.
- **P2 · Observed — Separate scenarios from baseline receipts.** The top-five-late-client scenario shows gross hypothetical receipts beside probability-weighted baseline cash. Explain the assumptions and show a like-for-like delta rather than inviting direct comparison.
- **P2 · Observed — Define “top five late clients.”** The displayed selection contained five clients tied at one late invoice. State the ranking and tie-break, or let reviewers rank by outstanding exposure, delay, or forecast contribution.
- **P2 · Design — Consolidate scenario amounts by week and display currency.** Avoid repeated same-week cards, especially after conversion to USD. Show one weekly total with expandable client contributions.
- **P2 · Observed — Keep the demo date persistent.** The snapshot was Nov 11 during a Nov 12 review, while several labels said today. Use “Demo as of Nov 11” consistently and distinguish snapshot date, model version time, and actual refresh time.
- **P2 · Design — Explain demo session persistence and reset scope.** Show whether edits and simulations belong to this browser session, what survives reload, and exactly what reset changes. Reviewers should be able to repeat a story predictably.
- **P2 · Observed — Separate uncertainty from the predicted date.** Low-confidence or beyond-horizon labels should be badges alongside quantiles, not substitutes for an otherwise available date. Explain missing P50/P90 as insufficient cumulative settlement probability within the horizon.
- **P2 · Design — Improve probability precision and interpretation.** Render very small probabilities as “<1%” where appropriate, and avoid emphasizing an already-past due-date probability for overdue invoices. Keep full-settlement versus partial-receipt semantics adjacent to the value.
- **P2 · Design — Explain uncertainty operationally.** Connect wide/unbounded intervals to what the user should do next: investigate, request a promise, or review collections. Avoid leaving “Beyond 180 days” as an unexplained dead end.
- **P2 · Observed — Make invoice-level evidence inspectable.** Add chart hover/focus values, dates, and a compact probability table. The current static survival graphic requires the presenter to explain axes, quantiles, and missing bounds verbally.
- **P2 · Design — Connect policy reasons to visible facts.** Show concise fact → threshold → suggested action, with expandable detail. Explain cross-currency queue allocation and why a lower-probability invoice can receive a different reminder severity.
- **P2 · Observed — Replace the raw Undo UUID.** Display invoice number and client, with a link to the skipped item. A database identifier is unusable in a live review.
- **P2 · Design — Give bulk approval an inspectable summary.** Show eligible count, invoice identities, exact action effects, and per-item completion/failure. Distinguish no eligible friendly actions from a temporarily unavailable queue.
- **P2 · Observed — Separate automatic suggestions from collections work.** Give the collections lane its own count, filters, and navigation anchor. It currently becomes another long block beneath a dense action explanation.
- **P2 · Design — Complete the collections lifecycle.** Add a closed/archive view and reopen control for resolved or written-off reviews. Explain that workflow status is metadata and does not itself settle or write off the financial balance.
- **P2 · Design — Make follow-up work actionable.** Add owner, status, and follow-up-date filters; highlight overdue follow-ups and unassigned cases. Show last saved time and a clear success result on each edited case.
- **P2 · Design — Protect unsaved review work.** Mark dirty notes, retain them across refresh/failure, and explain save-on-blur versus explicit Save. Navigating between queue and invoice should not silently discard edits.
- **P2 · Observed — Use operation-specific busy states.** Generating a draft made unrelated buttons announce Drafting, Processing, and Checking simultaneously. Disable only conflicting actions and name the operation actually running.
- **P2 · Design — Show draft state and provenance accurately.** Expose original, AI-generated, human-edited, saved, and approved states, with revert and a clear saved indicator. Human edits should not look like untouched Gemini output.
- **P2 · Design — Preview the actual recipient experience.** Keep the internal review note separate from the PayPal-native reminder that recipients receive. Show the provider action/channel and what text is actually customer-facing before operator approval.
- **P2 · Design — Make reply review corrections explicit.** Show the original reply beside extracted intent, date, and assumptions; flag past/ambiguous dates and allow correction before approval. Keep the preview-to-approval boundary prominent.
- **P2 · Design — Give pending replies a lifecycle.** Surface pending previews, approved classifications, dismissed items, and superseded promises with timestamps. A single first-pending preview is insufficient when multiple replies accumulate.
- **P2 · Design — Explain dispute and promise effects.** Show which forecast/action changed, why reminders paused, and what resolving or replacing the state will do. Include before/after evidence rather than only a success message.
- **P2 · Design — Preview payment simulations precisely.** Show the exact amount and resulting outstanding balance before full/half payment, and keep a route back to the changed invoice. An explicit amount option would demonstrate partial payments more flexibly than fixed 50%.
- **P2 · Design — Enrich the audit timeline.** Include actor, channel, note/classification changes, collection-status transitions, and consistent time zones. Allow event-type filtering and links from outcome summaries to the underlying events.
- **P2 · Observed — Preserve navigation context.** “All invoices” and client-portfolio returns can lose the original search, filters, page, and scroll. Carry a return context through invoice/client links and tour transitions.
- **P2 · Observed — Correct the client delay-history ordering label.** Bars are sorted by settlement date, while the explanation refers to invoice order. Label the actual ordering or change the sort, and show dates rather than only numbered bars.
- **P2 · Design — Make client evidence directly navigable.** Link reminder/payment examples to their invoice and event timeline. Keep the descriptive, noncausal interpretation beside claims about payment after a reminder.
- **P2 · Observed — Make terms results useful when 14-day probabilities are all zero.** Show 30/60/90-day probabilities and an explicit days-earlier/later delta. Include the representative issue date, amount, and baseline so reviewers can interpret the scenario.
- **P2 · Design — Add uncertainty to terms comparisons when supported.** Retain the existing noncausal caveat and show intervals or a clear unavailable state. Avoid visual certainty from single dates or redundant comparisons of identical baseline terms.
- **P2 · Observed — Explain the two headline error measures.** Issue-origin and later conditional-checkpoint errors use different cohorts and forecast origins. Put origins, sample counts, and plain-language definitions beside the values.
- **P2 · Observed — Present drift recall beside precision.** The headline precision is 55.6% on nine alerts, while recall is 33.3% deeper in the report. Expose both and their counts so alert usefulness is not overstated.
- **P2 · Design — Show interval sharpness and exclusions with coverage.** High coverage alone does not establish a useful forecast. Pair target/observed coverage with interval width, unbounded cases, and small-sample warnings.
- **P2 · Observed — Split the giant metrics disclosure.** Group model selection, calibration, drift, business scenarios, and external-data checks into readable sections. Keep the selected-model result separate from comparator explanations.
- **P2 · Design — Explain the deployed model using its own evidence.** Comparator LightGBM feature importance and SHAP are correctly labeled, but reviewers still need a concise explanation of the deployed Cox forecast and its limits.
- **P2 · Design — Make the PayPal integration demonstrable.** Provide an obvious operator route with a short create → pay → webhook/sync → balance-update checklist and timestamped evidence. Public simulation should be clearly connected to the real Sandbox capability.
- **P2 · Code — Add an operator sign-out control.** A logout API exists, but no visible UI logout was found. Show session state, Sandbox mode, and a usable sign-out action in the authenticated interface.

### P3 — polish, accessibility, and repeatability

- **P3 · Observed — Reduce duplicated dashboard content.** Hero balances repeat open-book totals and push the invoice work area far down. Keep the essential summary compact and reveal supporting breakdowns on demand.
- **P3 · Observed — Prevent monetary truncation.** Horizon cards clipped amounts with ellipses at the tested desktop width. Resize/wrap currency rows and verify small screens and zoom preserve complete money values.
- **P3 · Observed — Standardize currency order and FX cues.** Use a consistent EUR/GBP/USD order across cards, brief, and scenarios. Keep “illustrative USD equivalent” visible whenever converted totals appear.
- **P3 · Observed — Replace the raw status multiselect.** Use readable status chips or a clear checkbox popover with selected counts. Display “Partially paid” and “Not due” rather than backend constants.
- **P3 · Design — Align table affordances with actual behavior.** Add direct header sorting or make the sort toolbar unmistakable. Give the client invoice table search/sort and clearly bound pagination after filter changes.
- **P3 · Observed — Put important row actions inside the initial viewport.** The action column sits beyond horizontal scrolling while invoice/client columns remain pinned. Use a compact visible action affordance and a clear overflow cue.
- **P3 · Design — Explain and preserve grid customization.** Group optional columns, offer reset defaults, and remember the chosen layout. Include P10/P50/P90 definitions next to the column choices.
- **P3 · Design — Make export scope explicit.** State that CSV includes all filtered rows and include as-of date, original currency, active filters, and forecast definitions. Expose progress/error feedback for a slow export.
- **P3 · Design — Improve the lazy client selector.** Show loading, no matches, current selection, and a clear selection-reset control. Verify outside-click, Escape, keyboard selection, and long-name behavior.
- **P3 · Observed — Make story loading honest.** A named tour CTA can initially target the fallback before live story lookup finishes. Label loading/live/fallback destinations explicitly so a fast click does not silently change the demo mode.
- **P3 · Observed — Remove duplicate tour navigation.** Tour overview pages show both the persistent bar and another previous/next control set. Use one clear progress system and make “Continue” versus “Open this screen” unambiguous.
- **P3 · Design — Make packaged fallback reachable and versioned.** Add a stable header link, snapshot/build version, and clear live-data difference. Explain that this API-independent route still needs the web app unless deliberately cached for offline use.
- **P3 · Design — Make fallback examples easier to demonstrate.** Allow read-only detail expansion for the named invoices and show the relevant example per stop. The current static explanation is useful recovery but does not reproduce the interactive product.
- **P3 · Design — Replace hover-only evidence.** Provide focus/tap access and tabular alternatives for client delay bars and forecast charts. Test tooltip persistence, focus visibility, and touch targets during the redesign.
- **P3 · Design — Verify accessibility across dense controls.** Audit labels, live announcements for saves/errors, contrast, focus order, table semantics, and non-color status cues. The AX snapshot alone is insufficient to certify or condemn these controls.
- **P3 · Design — Add responsive and zoom acceptance criteria.** Test narrow screens, long client names, multi-currency amounts, expanded disclosures, and 200% zoom. Ensure sticky elements do not cover filters or dialogs.
- **P3 · Observed — Translate report terminology.** Explain Cox, IBS, censoring, calibration, drift, and quantiles in place; replace underscore metric names. Rename “issue-date error” to clarify that it measures a payment forecast made at issue.
- **P3 · Design — Qualify small and unequal evaluation cohorts.** Show N per public-data row and warning states for tiny age/currency buckets. Avoid presenting a one-case 100% result with the same visual authority as a large cohort.
- **P3 · Observed + Code — Describe review timing precisely.** The timer starts from interaction rather than complete reading and includes service delay. Label the metric accordingly and retain the existing no-baseline/no-time-saved caveat.
- **P3 · Design — Make error messages task-specific.** Replace raw timeout/provider wording with the affected task, retained state, and next step. Avoid duplicate last-success banners and unrelated error messages covering an entire page.
- **P3 · Code — Synchronize continuity docs.** Resolve stale Gemini-unused and keepalive-frequency notes, and distinguish unexposed features from nonexistent routes. Keep README, handoff, execution status, and walkthrough consistent with the deployed build.
- **P3 · Design — Add a focused pre-demo regression gate.** Check all five stops, cash-card/list consistency, skip/open/undo, save/reload, reply approval recovery, and fallback contents. Include a controlled cold-start check without relying on the keepalive schedule.

## Reproduction and implementation notes

- Cash drilldown: `app/portfolio-client.tsx:528` sends the card's week as `predicted_week`; `services/api/app/main.py:1515` filters `forecast.quantiles.p50.date`. This is a semantic mismatch even when both paths succeed.
- Skip/undo: Daybreak invoice `c956d0b6-5f5f-4e72-a67b-6647d43829bb`; skip in queue, open invoice detail, then Undo. The replacement conflict is explicit at `services/api/app/main.py:2847`.
- Collections: Willow Metric invoice LP-41-084 was given owner `Demo reviewer` and note `Review walkthrough: requested invoice copy.` in the isolated synthetic session. Both were present after full page reload. The post-save refresh timed out; the save itself succeeded.
- Reply: Daybreak's existing promise preview was approved once; the request timed out and a reload still showed the preview pending. No second approval was attempted, and the audit does not assert that a hidden write succeeded.
- Clock: selecting Nov 10 timed out and restored Nov 11 with an explicit last-success message. This is a latency finding, not the previously fixed incorrect-date rendering defect.
- Coverage: grouped coverage is computed by `ml/evaluation.py:60`; inspect the currency report construction against the finite-interval headline eligibility rule before changing the UI wording.
- Client chart: `app/clients/[id]/client-detail.tsx:82` sorts by `settled_at`; hover titles contain the precise date/delay, while visible bars use ordinal labels.
- Brief gain wording is in `app/portfolio-client.tsx:475`. Logout exists at `services/api/app/main.py:1275`; a visible logout control was not found.

This is an audit only. No application implementation was changed. PayPal transaction flows, mobile layouts, full keyboard/screen-reader behavior, and cross-browser behavior remain unverified; the relevant bullets are proposed acceptance criteria rather than claimed reproduced defects.
