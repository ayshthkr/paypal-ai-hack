import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const baseUrl = (process.env.SCREENSHOT_BASE_URL || "https://latepayer-web.onrender.com").replace(/\/$/, "");
const outputRoot = path.resolve(process.env.SCREENSHOT_DIR || "artifacts/screenshots/review-2026-11-12");
const deployedRevision = process.env.DEPLOYED_APP_REVISION || "not supplied";
const launchOptions = process.env.CHROME_BIN
  ? { headless: true, executablePath: process.env.CHROME_BIN }
  : { headless: true, channel: process.env.SCREENSHOT_BROWSER_CHANNEL || "chrome" };
const browser = await chromium.launch(launchOptions);
const context = await browser.newContext({
  viewport: { width: 1680, height: 1050 },
  deviceScaleFactor: 1.5,
  timezoneId: "Asia/Kolkata",
  colorScheme: "light",
});
const page = await context.newPage();
page.setDefaultTimeout(18_000);

const completed = [];
const skipped = [];

async function navigate(route) {
  await page.goto(new URL(route, `${baseUrl}/`).href, { waitUntil: "domcontentloaded", timeout: 90_000 });
  await page.locator("h1").first().waitFor({ state: "visible", timeout: 60_000 });
}

async function save(name, { fullPage = false, selector } = {}) {
  const destination = path.join(outputRoot, name);
  await mkdir(path.dirname(destination), { recursive: true });
  const target = selector ? (typeof selector === "string" ? page.locator(selector) : selector) : null;
  if (target) await target.scrollIntoViewIfNeeded();
  if (target) await target.screenshot({ path: destination, type: "jpeg", quality: 91 });
  else await page.screenshot({ path: destination, type: "jpeg", quality: 91, fullPage });
  completed.push(name);
  console.log(`saved ${name}`);
}

async function optional(name, action) {
  try {
    await action();
  } catch (error) {
    const message = error instanceof Error ? error.message.split("\n")[0] : String(error);
    skipped.push({ name, reason: message });
    console.warn(`skipped ${name}: ${message}`);
  }
}

try {
  await navigate("/");
  await page.locator("#cash-forecast .cash-week").first().waitFor({ state: "visible", timeout: 90_000 });
  await save("01-dashboard/01-portfolio-overview.jpg");

  await optional("demo-reset-confirmation", async () => {
    await page.getByRole("button", { name: "Reset demo" }).click();
    const dialog = page.getByRole("dialog", { name: "Reset this browser’s demo session?" });
    await dialog.waitFor({ state: "visible" });
    await save("01-dashboard/14-demo-reset-confirmation.jpg");
    await dialog.getByRole("button", { name: "Cancel" }).click();
  });

  await optional("story-menu-expanded", async () => {
    await page.getByText("Demo stories", { exact: true }).click();
    await page.locator(".demo-story-menu a").first().waitFor({ state: "visible", timeout: 45_000 });
    await save("01-dashboard/06-demo-story-entry-points.jpg", { selector: ".demo-story-menu" });
  });

  await optional("illustrative-usd-equivalent", async () => {
    await page.getByRole("button", { name: "Show USD equivalent" }).click();
    await save("01-dashboard/07-illustrative-usd-equivalent.jpg");
    await page.getByRole("button", { name: "Show source currencies" }).click();
  });

  await optional("morning-brief-expanded", async () => {
    await page.getByRole("button", { name: "Expand full brief" }).click();
    await page.locator("#morning-brief").scrollIntoViewIfNeeded();
    await save("01-dashboard/02-morning-brief-expanded.jpg");
  });

  await optional("ask-answer", async () => {
    await page.getByRole("button", { name: "Which clients are paying later?" }).click();
    await page.locator("#ask-title").scrollIntoViewIfNeeded();
    await save("01-dashboard/03-ask-supported-answer.jpg");
  });

  await optional("cash-week-contributors", async () => {
    await page.locator("#cash-forecast button.cash-week").first().click();
    await page.locator("#cash-contributors").waitFor({ state: "visible", timeout: 90_000 });
    await page.locator("#cash-contributors").scrollIntoViewIfNeeded();
    await save("01-dashboard/04-probability-mass-invoice-contributors.jpg");
  });

  await optional("empty-search-and-global-scope", async () => {
    await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 90_000 });
    await page.getByRole("textbox", { name: "Search invoices" }).fill("no-such-demo-invoice-987654");
    await page.getByText(/No matching invoices\./).first().waitFor({ state: "visible", timeout: 90_000 });
    await save("01-dashboard/05-empty-search-global-cards.jpg");
  });

  await optional("filter-popover-and-active-chips", async () => {
    await navigate("/?view=open&statuses=OVERDUE&due_from=2026-01-01&age_bucket=late_90_plus");
    await page.locator(".active-filter-chips").waitFor({ state: "visible", timeout: 90_000 });
    await page.locator("#invoice-grid").scrollIntoViewIfNeeded();
    await save("01-dashboard/08-active-filter-chips-and-clear-all.jpg", { selector: "#invoice-grid" });
    const statusFilter = page.locator(".status-filter");
    await statusFilter.locator("summary").click();
    await save("01-dashboard/09-readable-status-filter-popover.jpg", { selector: ".status-filter .column-menu" });
    await statusFilter.locator("summary").click();
    const clientSearch = page.getByRole("combobox", { name: "Search clients" });
    await clientSearch.fill("zz-no-matching-demo-client");
    await page.getByText("No matching clients.", { exact: true }).waitFor({ state: "visible", timeout: 30_000 });
    await save("01-dashboard/10-client-picker-no-results.jpg", { selector: "#client-options" });
  });

  await optional("column-preferences-menu", async () => {
    await navigate("/");
    await page.locator("#invoice-grid").scrollIntoViewIfNeeded();
    const columns = page.locator("#invoice-grid details.column-chooser:not(.status-filter)");
    await columns.locator("summary").click();
    await page.locator(".column-menu-wide").waitFor({ state: "visible" });
    await save("01-dashboard/11-column-preferences-and-quantile-help.jpg", { selector: ".column-menu-wide" });
  });

  await optional("scenario-like-for-like-and-weekly-breakdown", async () => {
    await navigate("/");
    await page.getByRole("button", { name: "Show weekly comparison" }).click();
    await page.locator(".scenario-comparison-grid").waitFor({ state: "visible", timeout: 45_000 });
    const scenario = page.locator(".panel").filter({ hasText: "If the top five late clients" }).last();
    await save("01-dashboard/12-hypothetical-versus-baseline-delta.jpg", { selector: ".scenario-comparison-grid" });
    const firstWeek = scenario.locator("details.cash-week").first();
    if (await firstWeek.count()) {
      await firstWeek.locator("summary").click();
      await save("01-dashboard/13-weekly-client-contributions-expanded.jpg", { selector: scenario });
    }
  });

  await navigate("/actions");
  await page.locator("#action-approval .queue-card").first().waitFor({ state: "visible", timeout: 120_000 });
  const detailHref = await page.locator("#action-approval a[href^='/invoices/']").first().getAttribute("href");
  await save("02-actions/01-ranked-action-queue.jpg");

  await optional("approval-review-dialogs", async () => {
    await page.route("**/api/action-queue", async route => {
      const upstream = await route.fetch();
      if (!upstream.ok()) return route.fulfill({ response: upstream });
      const payload = await upstream.json();
      const source = payload.items?.find(item => item.recommendation) || {
        id: "screenshot-invoice-101", invoice_number: "LP-DEMO-101", client_name: "Harborlight Design",
        client_id: "screenshot-client-101", currency: "USD", outstanding: "1250.00", due_date: "2026-11-15",
        decision: { action: "friendly_reminder", reason: "Due soon.", expected_cash_gain: 125 },
        recommendation: { id: "screenshot-recommendation-101", action: "friendly", state: "proposed", tone: "friendly", edited_text: "A reminder note for internal review.", reason: "Payment is unlikely by the due date." },
      };
      const makeItem = (suffix, number, client) => ({
        ...source,
        id: `screenshot-invoice-${suffix}`,
        invoice_number: number,
        client_name: client,
        client_email: `${suffix}@example.test`,
        source: "paypal",
        recommendation: { ...source.recommendation, id: `screenshot-recommendation-${suffix}`, action: "friendly", state: "proposed", tone: "friendly" },
      });
      payload.items = [makeItem("101", "LP-DEMO-101", "Harborlight Design"), makeItem("102", "LP-DEMO-102", "Verdant Market")];
      payload.daily_cap = 1;
      payload.pending_count = 2;
      payload.synthetic_demo = false;
      payload.skipped = [];
      await route.fulfill({ response: upstream, body: JSON.stringify(payload) });
    });
    try {
      await navigate("/actions");
      await page.getByRole("button", { name: "Approve eligible friendly reminders" }).click();
      const bulkDialog = page.getByRole("dialog", { name: "Review friendly reminders before approval" });
      await bulkDialog.waitFor({ state: "visible" });
      await save("02-actions/06-bulk-approval-review-dialog.jpg");
      await bulkDialog.getByRole("button", { name: "Cancel" }).click();

      await page.locator(".queue-card").first().getByRole("button", { name: "Review PayPal reminder" }).click();
      const providerDialog = page.getByRole("dialog", { name: "Approve this PayPal Sandbox reminder?" });
      await providerDialog.waitFor({ state: "visible" });
      await save("02-actions/07-paypal-reminder-approval-dialog.jpg");
      await providerDialog.getByRole("button", { name: "Cancel" }).click();
    } finally {
      await page.unroute("**/api/action-queue");
    }
  });

  await optional("human-edited-draft-provenance", async () => {
    const card = page.locator(".queue-card").filter({ has: page.locator(".draft-provenance") }).first();
    const editor = card.locator("textarea").first();
    if (!(await editor.count()) || !(await editor.isEnabled())) throw new Error("No editable synthetic reminder is available.");
    const original = await editor.inputValue();
    await editor.fill(`${original}\nHuman-edited for reviewer walkthrough.`);
    await page.getByText("Saved to server", { exact: true }).waitFor({ state: "visible", timeout: 15_000 });
    await save("02-actions/05-human-edited-draft-provenance.jpg", { selector: card.locator(".draft-provenance").first() });
    await card.getByRole("button", { name: "Revert to original draft" }).click();
  });

  await optional("policy-explanation", async () => {
    await page.locator("details.policy-thresholds summary").click();
    await save("02-actions/02-policy-fact-threshold-action.jpg");
  });

  await optional("skip-and-reconcile", async () => {
    await page.getByRole("button", { name: "Skip", exact: true }).first().click();
    await page.getByRole("link", { name: "Open skipped record" }).first().waitFor({ state: "visible", timeout: 30_000 });
    await save("02-actions/03-skipped-record-with-invoice-identity.jpg");
    const skippedHref = await page.getByRole("link", { name: "Open skipped record" }).first().getAttribute("href");
    if (skippedHref) {
      await navigate(skippedHref);
      await save("03-invoices/01-skipped-invoice-and-timeline.jpg");
      await navigate("/actions");
      await page.getByRole("link", { name: "Open skipped record" }).first().waitFor({ state: "visible", timeout: 60_000 });
      await save("02-actions/04-skip-open-reconciliation.jpg");
    }
  });

  await navigate("/collections");
  await page.getByText(/open collections cases/, { exact: false }).waitFor({ state: "visible", timeout: 90_000 });
  await save("04-collections/01-filterable-open-workbench.jpg");
  await optional("collections-dirty-and-saved-states", async () => {
    const card = page.locator(".queue-list > .collection-card").first();
    await card.getByLabel("Owner").fill("Demo reviewer");
    await card.getByRole("status").getByText(/Unsaved changes/).waitFor({ state: "visible" });
    await save("04-collections/03-unsaved-review-retained.jpg", { selector: card });
    await card.getByRole("button", { name: "Save review" }).click();
    await card.getByRole("status").getByText(/^Saved ·/).waitFor({ state: "visible", timeout: 90_000 });
    await save("04-collections/04-saved-review-state.jpg", { selector: card });
  });
  await optional("collections-archive", async () => {
    await page.getByRole("button", { name: /Closed archive/ }).click();
    await save("04-collections/02-closed-archive.jpg");
  });

  await optional("invoice-provider-approval-dialogs", async () => {
    if (!detailHref) throw new Error("No invoice detail is available for the approval preview.");
    const invoiceId = new URL(detailHref, baseUrl).pathname.split("/").filter(Boolean).pop();
    if (!invoiceId) throw new Error("Could not identify the invoice for the approval preview.");
    let mode = "friendly";
    const invoiceMatcher = url => url.pathname === `/api/invoices/${encodeURIComponent(invoiceId)}`;
    await page.route("**/api/auth/me", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authenticated: true, username: "reviewer-preview" }) }));
    await page.route(invoiceMatcher, async route => {
      const upstream = await route.fetch();
      if (!upstream.ok()) return route.fulfill({ response: upstream });
      const payload = await upstream.json();
      payload.source = "paypal";
      payload.client_email ||= "sandbox-recipient@example.test";
      payload.simulation_disabled = false;
      payload.disputed = false;
      payload.decision = { ...payload.decision, action: mode, reason: mode === "friendly" ? "Review a friendly reminder." : "Review partial-payment access." };
      payload.recommendation = {
        ...(payload.recommendation || {}),
        id: `screenshot-${mode}-recommendation`, action: mode, tone: mode === "friendly" ? "friendly" : "partial",
        state: "proposed", reason: "Generated for a non-mutating Sandbox approval preview.",
        draft_text: "A friendly reminder to review the outstanding PayPal invoice.",
        edited_text: "A friendly reminder to review the outstanding PayPal invoice.",
        draft_source: "template", draft_model: null, drafted_at: null, approved_text: null,
      };
      await route.fulfill({ response: upstream, body: JSON.stringify(payload) });
    });
    try {
      await navigate(detailHref);
      await page.getByRole("button", { name: "Review PayPal reminder" }).click();
      const reminderDialog = page.getByRole("dialog", { name: "Approve this PayPal Sandbox reminder?" });
      await reminderDialog.waitFor({ state: "visible" });
      await save("03-invoices/09-paypal-reminder-approval-dialog.jpg");
      await reminderDialog.getByRole("button", { name: "Cancel" }).click();

      mode = "offer_partial_payment";
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByRole("button", { name: "Review PayPal update" }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "Review PayPal update" }).click();
      const partialDialog = page.getByRole("dialog", { name: "Enable partial payments in PayPal Sandbox?" });
      await partialDialog.waitFor({ state: "visible" });
      await save("03-invoices/10-paypal-partial-payment-approval-dialog.jpg");
      await partialDialog.getByRole("button", { name: "Cancel" }).click();
    } finally {
      await page.unroute(invoiceMatcher);
      await page.unroute("**/api/auth/me");
    }
  });

  await optional("invoice-details", async () => {
    if (!detailHref) throw new Error("No invoice link was found in the action queue.");
    await navigate(detailHref);
    await save("03-invoices/02-invoice-forecast-and-next-action.jpg");
    await optional("partial-payment-amount-preview", async () => {
      const panel = page.locator(".payment-simulation");
      const amount = panel.locator("input[type=number]");
      const max = Number(await amount.getAttribute("max"));
      await amount.fill((max / 3).toFixed(2));
      await panel.locator(".payment-preview").waitFor({ state: "visible" });
      await save("03-invoices/06-partial-payment-amount-and-balance-preview.jpg", { selector: ".payment-simulation" });
      if (await panel.getByRole("button", { name: /Record this partial payment/ }).isEnabled()) {
        await panel.getByRole("button", { name: /Record this partial payment/ }).click();
        await page.getByRole("status").getByText(/Synthetic demo only; no PayPal payment was made/).waitFor({ state: "visible", timeout: 45_000 });
        await save("03-invoices/07-simulated-partial-payment-result.jpg", { selector: ".payment-simulation" });
      }
    });
    await page.locator(".curve-wrap").scrollIntoViewIfNeeded();
    await save("03-invoices/03-survival-curve-and-partial-settlement-explanation.jpg");
    await optional("survival-probability-table", async () => {
      const values = page.locator(".curve-values");
      await values.evaluate(details => { details.open = true; });
      await values.locator("table").waitFor({ state: "visible" });
      await values.scrollIntoViewIfNeeded();
      await save("03-invoices/04-dated-survival-probability-table.jpg", { selector: ".curve-values" });
    });
    await optional("reply-classification-preview", async () => {
      const reply = page.getByRole("textbox", { name: "Paste client reply" });
      if (await reply.count()) {
        await reply.fill("We will pay Friday.");
        await page.getByRole("button", { name: "Classify reply" }).click();
        await page.locator(".reply-review").waitFor({ state: "visible", timeout: 45_000 });
        await save("03-invoices/05-reply-classification-review.jpg");
        const approve = page.getByRole("button", { name: "Approve classification" });
        if (await approve.isEnabled()) {
          await approve.click();
          await page.getByText(/Promise recorded|Reminders paused/).first().waitFor({ state: "visible", timeout: 45_000 });
          await save("03-invoices/08-approved-reply-lifecycle.jpg", { selector: "#reply-triage" });
        }
      }
    });
  });

  await optional("client-detail", async () => {
    await navigate("/collections");
    const verdantHref = await page.getByRole("link", { name: "Verdant Market" }).getAttribute("href");
    if (!verdantHref) throw new Error("Verdant Market client link is not available.");
    await navigate(verdantHref);
    await save("05-clients/01-client-timing-and-exposure.jpg");
    await optional("client-terms-scenario", async () => {
      const terms = page.getByText("What if the next invoice had different terms?", { exact: true });
      await terms.scrollIntoViewIfNeeded();
      await page.getByRole("button", { name: "Compare scenarios" }).click();
      await page.locator(".what-if-grid").waitFor({ state: "visible", timeout: 45_000 });
      await save("05-clients/02-terms-scenario-horizons.jpg");
      const probabilities = page.locator(".client-invoice-table").filter({ hasText: "Paid within 30 days" });
      await probabilities.scrollIntoViewIfNeeded();
      await save("05-clients/03-terms-scenario-30-60-90-day-comparison.jpg", { selector: probabilities });
    });
  });

  await optional("performance-overview", async () => {
    await navigate("/performance");
    await page.locator(".performance-section-nav").waitFor({ state: "visible", timeout: 90_000 });
    await save("06-performance/01-performance-overview.jpg");
    for (const [id, file] of [
      ["model-selection", "02-model-selection-validation"],
      ["calibration", "03-calibration-interval-usefulness"],
      ["drift-evaluation", "04-client-drift-evaluation"],
      ["business-scenarios", "05-business-scenarios"],
      ["external-checks", "06-external-data-comparators"],
    ]) {
      await optional(file, async () => {
        const section = page.locator(`#${id}`);
        await section.locator("summary").click();
        await section.scrollIntoViewIfNeeded();
        await save(`06-performance/${file}.jpg`);
      });
    }
  });

  await optional("tour-stops", async () => {
    await navigate("/tour?stop=1");
    for (let stop = 1; stop <= 5; stop += 1) {
      await save(`07-guided-tour/0${stop}-stop-${["portfolio", "verdant-outlier", "action-queue", "model-performance", "reply-triage"][stop - 1]}.jpg`);
      if (stop < 5) {
        await page.getByRole("button", { name: "Next stop" }).click();
        await page.locator(`.guided-stop[aria-label="Stop ${stop + 1} of 5"]`).waitFor({ state: "visible" });
      }
    }
  });

  await optional("packaged-fallback", async () => {
    await page.evaluate(() => sessionStorage.removeItem("latepayer-tour-stop"));
    await navigate("/offline-demo");
    await save("08-fallback/01-versioned-read-only-fallback.jpg");
    await optional("fallback-invoice-evidence", async () => {
      await page.evaluate(() => sessionStorage.setItem("latepayer-tour-stop", "5"));
      await navigate("/offline-demo?stop=5");
      await save("08-fallback/02-reply-triage-example.jpg");
      await page.locator(".fallback-invoice-detail summary").click();
      await page.locator(".fallback-invoice-detail .fallback-evidence-grid").waitFor({ state: "visible" });
      await save("08-fallback/03-expanded-invoice-evidence.jpg");
    });
  });

  await optional("operator-login", async () => {
    await navigate("/login");
    await save("09-operator/01-sandbox-operator-login.jpg");
  });

  await optional("authenticated-operator-checklist", async () => {
    await page.route("**/api/auth/me", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ authenticated: true, username: "reviewer-preview" }) }));
    await page.route("**/api/paypal/recipients", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [{ id: "allowlisted-reviewer", name: "Sandbox Reviewer", email: "reviewer@example.test" }] }) }));
    await page.route("**/api/sync/status", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ last_run: null, webhook_failures: 0, webhook_events: [] }) }));
    try {
      await navigate("/operator");
      await page.getByText("Authenticated · PayPal Sandbox", { exact: true }).waitFor({ state: "visible" });
      await page.locator(".operator-checklist").waitFor({ state: "visible" });
      await save("09-operator/02-authenticated-sandbox-checklist.jpg");
    } finally {
      await page.unroute("**/api/auth/me");
      await page.unroute("**/api/paypal/recipients");
      await page.unroute("**/api/sync/status");
    }
  });

  await optional("narrow-portfolio", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await navigate("/");
    await save("10-responsive/01-narrow-portfolio.jpg");
    await page.setViewportSize({ width: 1680, height: 1050 });
  });

  await optional("200-percent-zoom-equivalent", async () => {
    await page.setViewportSize({ width: 840, height: 525 });
    await navigate("/");
    await save("10-responsive/02-200-percent-zoom-equivalent.jpg");
    await page.setViewportSize({ width: 1680, height: 1050 });
  });
} finally {
  await browser.close();
  completed.sort((left, right) => left.localeCompare(right));
  const manifest = [
    "# Deployed demo screenshots",
    "",
    `Captured from ${baseUrl} on 2026-11-12 after publishing app commit ${deployedRevision}. Public pages use synthetic demo data; no PayPal transaction or reminder was sent.`,
    "",
    "Screenshots are numbered by workflow and then by page order. Images are viewport captures at a 1680 × 1050 CSS viewport and 1.5× device scale, except the narrow portfolio capture (390 × 844 CSS pixels) and the 200% zoom equivalent (840 × 525 CSS pixels).",
    "",
    "## Captured",
    "",
    ...completed.map((name) => `- [${name}](./${name})`),
    "",
    "## Not captured",
    "",
    ...(skipped.length ? skipped.map((entry) => `- ${entry.name}: ${entry.reason}`) : ["- None"]),
    "",
  ].join("\n");
  await mkdir(outputRoot, { recursive: true });
  await writeFile(path.join(outputRoot, "README.md"), manifest);
  console.log(`Captured ${completed.length} screenshots; manifest written to ${path.join(outputRoot, "README.md")}`);
}
