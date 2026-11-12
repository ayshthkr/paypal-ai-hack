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
const apiSnapshots = new Map();
page.on("response", (response) => {
  const request = response.request();
  const pathname = new URL(response.url()).pathname;
  if (request.method() !== "GET" || !pathname.startsWith("/api/") || !response.ok()) return;
  void response.json().then((payload) => apiSnapshots.set(pathname, payload)).catch(() => undefined);
});

let actionQueueSnapshot = null;
let invoiceSnapshot = null;
let invoiceListSnapshot = null;
let clientSnapshot = null;
let evaluationSnapshot = null;
let whatIfSnapshot = null;
let detailHref = null;
let verdantHref = null;
let invoiceFixtureMode = "demo";
let simulatedPaymentAmount = null;
let replyFixture = null;

async function waitForApiSnapshot(pathname, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (!apiSnapshots.has(pathname) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (!apiSnapshots.has(pathname)) throw new Error(`No successful ${pathname} response was captured.`);
  return structuredClone(apiSnapshots.get(pathname));
}

function fulfillJson(route, payload) {
  return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
}

function makeQueueFixture(kind) {
  if (!actionQueueSnapshot?.items?.length) throw new Error("No live queue snapshot is available for the browser-local display fixture.");
  const payload = structuredClone(actionQueueSnapshot);
  const source = payload.items.find((item) => item.recommendation) || payload.items[0];
  const second = payload.items.find((item) => item.id !== source.id && item.recommendation) || source;
  const provider = kind === "provider";
  const makeItem = (base, suffix, clientName) => ({
    ...base,
    id: suffix === "101" ? base.id : base.id === source.id ? `${base.id}-screenshot-${suffix}` : base.id,
    invoice_number: `LP-DEMO-${suffix}`,
    client_name: clientName,
    client_email: `${suffix}@example.test`,
    source: provider ? "paypal" : "demo-synthetic",
    currency: "USD",
    outstanding: "1250.00",
    due_date: "2026-11-15",
    decision: { ...base.decision, action: "friendly", reason: "Payment is unlikely by the due date.", expected_cash_gain: 125 },
    recommendation: {
      ...(base.recommendation || {}),
      id: `screenshot-${kind}-recommendation-${suffix}`,
      action: "friendly",
      tone: "friendly",
      state: "proposed",
      reason: "Review the grounded payment-timing evidence before acting.",
      draft_text: "Hello, this is a reminder that the invoice is due soon. Please let us know if you have any questions.",
      edited_text: "Hello, this is a reminder that the invoice is due soon. Please let us know if you have any questions.",
      draft_source: "template",
      draft_model: null,
      drafted_at: "2026-11-12T09:00:00Z",
      approved_text: null,
      approved_at: null,
    },
  });
  payload.items = [
    makeItem(source, "101", "Harborlight Design"),
    makeItem(second, "102", "Verdant Market"),
  ];
  payload.daily_cap = 1;
  payload.count = payload.items.length;
  payload.pending_count = payload.items.length;
  payload.synthetic_demo = !provider;
  payload.skipped = [];
  payload.skipped_count = 0;
  return payload;
}

function makeInvoiceFixture() {
  if (!invoiceSnapshot) throw new Error("No invoice snapshot is available for the browser-local display fixture.");
  const payload = structuredClone(invoiceSnapshot);
  if (invoiceFixtureMode === "demo") {
    payload.source = "demo";
    payload.simulation_disabled = false;
  }
  if (invoiceFixtureMode === "friendly" || invoiceFixtureMode === "partial") {
    const partial = invoiceFixtureMode === "partial";
    payload.source = "paypal";
    payload.client_email = "sandbox-recipient@example.test";
    payload.simulation_disabled = false;
    payload.disputed = false;
    payload.decision = {
      ...payload.decision,
      action: partial ? "offer_partial_payment" : "friendly",
      reason: partial ? "Review a partial-payment setting for this eligible Sandbox invoice." : "Review a friendly Sandbox reminder for this invoice.",
    };
    payload.recommendation = {
      ...(payload.recommendation || {}),
      id: `screenshot-${invoiceFixtureMode}-recommendation`,
      action: partial ? "offer_partial_payment" : "friendly",
      tone: partial ? "partial" : "friendly",
      state: "proposed",
      reason: "Browser-local preview fixture for reviewer screenshots.",
      draft_text: "A friendly reminder to review the outstanding PayPal invoice.",
      edited_text: "A friendly reminder to review the outstanding PayPal invoice.",
      draft_source: "template",
      draft_model: null,
      drafted_at: "2026-11-12T09:00:00Z",
      approved_text: null,
    };
  }
  if (simulatedPaymentAmount != null) {
    const originalPaid = Number(invoiceSnapshot.paid || 0);
    const originalOutstanding = Number(invoiceSnapshot.outstanding || 0);
    const amount = Math.min(originalOutstanding, simulatedPaymentAmount);
    payload.paid = (originalPaid + amount).toFixed(2);
    payload.outstanding = Math.max(0, originalOutstanding - amount).toFixed(2);
    payload.status = Number(payload.outstanding) === 0 ? "PAID" : "PARTIALLY_PAID";
    payload.timeline = [
      ...(invoiceSnapshot.timeline || []),
      { kind: "Payment", at: "2026-11-12T09:15:00Z", detail: `Synthetic demo payment · ${payload.currency} ${amount.toFixed(2)}` },
    ];
  }
  if (replyFixture) {
    payload.reply_triages = [replyFixture, ...(invoiceSnapshot.reply_triages || []).filter((row) => row.id !== replyFixture.id)];
    if (replyFixture.state === "applied" && replyFixture.classification === "promise_to_pay") {
      payload.promised_payment_date = replyFixture.promised_payment_date;
    }
  }
  return payload;
}

async function waitForInvoiceNeighbors() {
  await page.locator(".invoice-navigation").getByText("Current portfolio view", { exact: true })
    .waitFor({ state: "visible", timeout: 90_000 });
}

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
    const verdantStory = page.locator(".demo-story-menu a").filter({ hasText: "Verdant Market" }).first();
    if (await verdantStory.count()) verdantHref = await verdantStory.getAttribute("href");
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

  apiSnapshots.delete("/api/action-queue");
  await navigate("/actions");
  await page.locator("#action-approval .queue-card").first().waitFor({ state: "visible", timeout: 120_000 });
  detailHref = await page.locator("#action-approval a[href^='/invoices/']").first().getAttribute("href");
  actionQueueSnapshot = await waitForApiSnapshot("/api/action-queue");
  await save("02-actions/01-ranked-action-queue.jpg");
  if (!detailHref) throw new Error("The action queue did not expose an invoice detail link.");
  const invoicePath = new URL(detailHref, baseUrl).pathname.replace(/^/, "/api");
  apiSnapshots.delete(invoicePath);
  apiSnapshots.delete("/api/invoices");
  await navigate(detailHref);
  await page.locator("#invoice-timeline").waitFor({ state: "visible", timeout: 90_000 });
  invoiceSnapshot = await waitForApiSnapshot(invoicePath);
  invoiceListSnapshot = await waitForApiSnapshot("/api/invoices");
  await waitForInvoiceNeighbors();

  const invoiceMatcher = (url) => url.pathname === invoicePath;
  const invoiceListMatcher = (url) => url.pathname === "/api/invoices" && url.searchParams.get("sort") === "action";
  const paymentMatcher = (url) => url.pathname === `/api/demo/invoices/${encodeURIComponent(invoiceSnapshot.id)}/payment`;
  const replyMatcher = (url) => url.pathname === `${invoicePath}/reply`;
  const replyApplyMatcher = (url) => /^\/api\/invoices\/[^/]+\/reply-triage\/[^/]+\/apply$/.test(url.pathname);
  await page.route(invoiceMatcher, (route) => fulfillJson(route, makeInvoiceFixture()));
  await page.route(invoiceListMatcher, (route) => fulfillJson(route, invoiceListSnapshot));
  await page.route(paymentMatcher, (route) => {
    simulatedPaymentAmount = Number(route.request().postDataJSON().amount);
    const outstanding = Math.max(0, Number(invoiceSnapshot.outstanding) - simulatedPaymentAmount);
    return fulfillJson(route, {
      invoice_id: invoiceSnapshot.id,
      status: outstanding === 0 ? "PAID" : "PARTIALLY_PAID",
      paid: (Number(invoiceSnapshot.paid || 0) + simulatedPaymentAmount).toFixed(2),
      outstanding: outstanding.toFixed(2),
      currency: invoiceSnapshot.currency,
      simulated: true,
      note: "Synthetic demo only; no PayPal payment was made.",
    });
  });
  await page.route(replyMatcher, (route) => {
    const body = route.request().postDataJSON();
    replyFixture = {
      id: "screenshot-reply-101",
      reply_text: body.reply,
      classification: "promise_to_pay",
      classifier: "rules",
      promised_payment_date: "2026-11-20",
      state: "pending",
      created_at: "2026-11-12T09:25:00Z",
      reviewed_at: null,
    };
    return fulfillJson(route, {
      ...replyFixture,
      reminders_paused: false,
      note: "Preview only. Review or correct the extracted type and date, then approve it to update invoice state.",
    });
  });
  await page.route(replyApplyMatcher, (route) => {
    if (replyFixture) replyFixture = { ...replyFixture, state: "applied", reviewed_at: "2026-11-12T09:30:00Z" };
    return fulfillJson(route, {
      id: replyFixture?.id || "screenshot-reply-101",
      state: "applied",
      classification: "promise_to_pay",
      promised_payment_date: replyFixture?.promised_payment_date || "2026-11-20",
      disputed: false,
      note: "Reply review applied. The invoice and reminder policy now reflect the approved classification.",
    });
  });

  await optional("client-detail-and-terms-scenario", async () => {
    if (!verdantHref) throw new Error("The dashboard story menu did not expose the Verdant Market detail link.");
    const storyPath = new URL(verdantHref, baseUrl).pathname;
    let clientHref = verdantHref;
    if (storyPath.startsWith("/invoices/")) {
      let storyInvoice = invoiceSnapshot;
      const storyInvoicePath = storyPath.replace(/^/, "/api");
      if (storyInvoicePath !== invoicePath) {
        apiSnapshots.delete(storyInvoicePath);
        await navigate(verdantHref);
        await page.locator("#invoice-timeline").waitFor({ state: "visible", timeout: 90_000 });
        storyInvoice = await waitForApiSnapshot(storyInvoicePath);
      }
      if (!storyInvoice?.client_id) throw new Error("The Verdant story invoice did not include its client identity.");
      clientHref = `/clients/${encodeURIComponent(storyInvoice.client_id)}`;
    }
    const clientPath = new URL(clientHref, baseUrl).pathname.replace(/^\/clients\//, "/api/facts/clients/");
    apiSnapshots.delete(clientPath);
    await navigate(clientHref);
    await page.getByText(/invoices · \d+ fully settled · as of/).waitFor({ state: "visible", timeout: 90_000 });
    clientSnapshot = await waitForApiSnapshot(clientPath);
    await save("05-clients/01-client-timing-and-exposure.jpg");
    await optional("client-terms-scenario", async () => {
      const responsePromise = page.waitForResponse((response) => new URL(response.url()).pathname === `${clientPath.replace("/api/facts/clients/", "/api/clients/")}/what-if` && response.request().method() === "POST", { timeout: 90_000 });
      await page.getByRole("button", { name: "Compare scenarios" }).click();
      await page.locator(".what-if-grid").waitFor({ state: "visible", timeout: 90_000 });
      const response = await responsePromise;
      if (!response.ok()) throw new Error(`Terms scenario returned ${response.status()}.`);
      whatIfSnapshot = await response.json();
      await save("05-clients/02-terms-scenario-horizons.jpg", { fullPage: true });
      const probabilities = page.locator(".client-invoice-table").filter({ hasText: "Paid within 30 days" });
      await probabilities.scrollIntoViewIfNeeded();
      await save("05-clients/03-terms-scenario-30-60-90-day-comparison.jpg", { selector: probabilities });
    });
  });

  apiSnapshots.delete("/api/evaluation");
  await navigate("/performance");
  await page.locator(".performance-section-nav").waitFor({ state: "visible", timeout: 90_000 });
  evaluationSnapshot = await waitForApiSnapshot("/api/evaluation");
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
  await page.route((url) => url.pathname === "/api/evaluation", (route) => fulfillJson(route, evaluationSnapshot));

  let activeQueueFixture = makeQueueFixture("provider");
  let mockedSkippedItem = null;
  let collectionSaveFixture = null;
  let collectionSaveInvoiceId = null;
  const queueMatcher = (url) => url.pathname === "/api/action-queue";
  const authMatcher = (url) => url.pathname === "/api/auth/me";
  const draftMatcher = (url) => /^\/api\/recommendations\/[^/]+\/draft$/.test(url.pathname);
  const skipMatcher = (url) => /^\/api\/recommendations\/[^/]+\/skip$/.test(url.pathname);
  const reviewMetricMatcher = (url) => /^\/api\/invoices\/[^/]+\/review-metric$/.test(url.pathname);
  const collectionMatcher = (url) => /^\/api\/invoices\/[^/]+\/collection-review$/.test(url.pathname);
  await page.route(queueMatcher, (route) => {
    const payload = structuredClone(activeQueueFixture);
    if (collectionSaveFixture && collectionSaveInvoiceId) {
      const record = [...(payload.collections_review || []), ...(payload.collections_closed || [])].find((item) => item.id === collectionSaveInvoiceId);
      if (record) record.collection_review = { ...collectionSaveFixture, updated_at: "2026-11-12T09:30:00Z" };
    }
    if (mockedSkippedItem) {
      payload.items = payload.items.filter((item) => item.id !== mockedSkippedItem.invoice_id);
      payload.skipped = [mockedSkippedItem, ...(payload.skipped || [])];
      payload.skipped_count = payload.skipped.length;
      payload.pending_count = payload.items.length;
    }
    return fulfillJson(route, payload);
  });
  await page.route(authMatcher, (route) => fulfillJson(route, { authenticated: true, username: "reviewer-preview" }));
  await page.route(draftMatcher, (route) => fulfillJson(route, { saved: true }));
  await page.route(skipMatcher, (route) => {
    const recommendationId = new URL(route.request().url()).pathname.split("/").at(-2);
    const item = activeQueueFixture.items.find((candidate) => candidate.recommendation?.id === recommendationId);
    if (item) mockedSkippedItem = { id: recommendationId, invoice_id: item.id, invoice_number: item.invoice_number, client_name: item.client_name };
    return fulfillJson(route, { state: "skipped", note: "Browser-local screenshot fixture." });
  });
  await page.route(reviewMetricMatcher, (route) => fulfillJson(route, { recorded: true }));
  await page.route(collectionMatcher, async (route) => {
    collectionSaveInvoiceId = new URL(route.request().url()).pathname.split("/").at(-2);
    collectionSaveFixture = route.request().postDataJSON();
    return fulfillJson(route, { saved: true, review: collectionSaveFixture });
  });

  await optional("approval-review-dialogs", async () => {
    activeQueueFixture = makeQueueFixture("provider");
    mockedSkippedItem = null;
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
  });

  await optional("human-edited-draft-provenance", async () => {
    activeQueueFixture = makeQueueFixture("synthetic");
    mockedSkippedItem = null;
    await navigate("/actions");
    await page.locator("#action-approval .queue-card").first().waitFor({ state: "visible", timeout: 90_000 });
    const card = page.locator("#action-approval .queue-card").first();
    await card.locator(".draft-provenance").waitFor({ state: "visible", timeout: 30_000 });
    const editor = card.locator("textarea").first();
    if (!(await editor.count()) || !(await editor.isEnabled())) throw new Error("The browser-local queue fixture has no editable reminder.");
    const original = await editor.inputValue();
    await editor.fill(`${original}\nHuman-edited for reviewer walkthrough.`);
    await page.getByText("Saved to server", { exact: true }).waitFor({ state: "visible", timeout: 15_000 });
    await save("02-actions/05-human-edited-draft-provenance.jpg", { selector: card.locator(".draft-provenance").first() });
    await card.getByRole("button", { name: "Revert to original draft" }).click();
  });

  await optional("policy-explanation", async () => {
    activeQueueFixture = makeQueueFixture("provider");
    mockedSkippedItem = null;
    await navigate("/actions");
    await page.locator("details.policy-thresholds summary").click();
    await save("02-actions/02-policy-fact-threshold-action.jpg");
  });

  await optional("skip-and-reconcile", async () => {
    activeQueueFixture = makeQueueFixture("provider");
    mockedSkippedItem = null;
    await navigate("/actions");
    await page.getByRole("button", { name: "Skip", exact: true }).first().click();
    await page.getByRole("link", { name: "Open skipped record" }).first().waitFor({ state: "visible", timeout: 30_000 });
    await save("02-actions/03-skipped-record-with-invoice-identity.jpg");
    const skippedHref = await page.getByRole("link", { name: "Open skipped record" }).first().getAttribute("href");
    if (skippedHref) {
      await navigate(skippedHref);
      await waitForInvoiceNeighbors();
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
    await card.getByLabel("Owner").fill("Screenshot reviewer");
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
    invoiceFixtureMode = "friendly";
    await navigate(detailHref);
    await waitForInvoiceNeighbors();
    await page.getByRole("button", { name: "Review PayPal reminder" }).click();
    const reminderDialog = page.getByRole("dialog", { name: "Approve this PayPal Sandbox reminder?" });
    await reminderDialog.waitFor({ state: "visible" });
    await save("03-invoices/09-paypal-reminder-approval-dialog.jpg");
    await reminderDialog.getByRole("button", { name: "Cancel" }).click();

    invoiceFixtureMode = "partial";
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForInvoiceNeighbors();
    await page.getByRole("button", { name: "Review PayPal update" }).click();
    const partialDialog = page.getByRole("dialog", { name: "Enable partial payments in PayPal Sandbox?" });
    await partialDialog.waitFor({ state: "visible" });
    await save("03-invoices/10-paypal-partial-payment-approval-dialog.jpg");
    await partialDialog.getByRole("button", { name: "Cancel" }).click();
    invoiceFixtureMode = "demo";
  });

  await optional("invoice-details", async () => {
    if (!detailHref) throw new Error("No invoice link was found in the action queue.");
    invoiceFixtureMode = "demo";
    simulatedPaymentAmount = null;
    replyFixture = null;
    await navigate(detailHref);
    await waitForInvoiceNeighbors();
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
        await save("03-invoices/07-simulated-partial-payment-result.jpg", { fullPage: true });
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
    await page.evaluate(() => {
      sessionStorage.removeItem("latepayer-tour-stop");
      sessionStorage.removeItem("latepayer-tour-active");
    });
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
    `Captured from ${baseUrl} on 2026-11-12 after publishing app commit ${deployedRevision}. Approval, payment, reply, collection-save, and authenticated-operator states use browser-local display fixtures; no PayPal transaction or reminder was sent.`,
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
