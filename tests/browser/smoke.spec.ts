import { test, expect } from "@playwright/test";

async function warmRead(page: import("@playwright/test").Page, path: string) {
  let lastStatus = "no response";
  const deadline = Date.now() + 90_000;
  while (Date.now() < deadline) {
    try {
      const response = await page.request.get(path, { timeout: Math.min(20_000, deadline - Date.now()) });
      if (response.ok()) return response;
      lastStatus = `HTTP ${response.status()}`;
    } catch (cause) {
      lastStatus = cause instanceof Error ? cause.message : String(cause);
    }
    if (Date.now() < deadline) await page.waitForTimeout(Math.min(2_500, deadline - Date.now()));
  }
  throw new Error(`Could not warm ${path} within 90 seconds: ${lastStatus}`);
}

test("portfolio, queue, client and invoice workflow stays within warm budgets", async ({ page }) => {
  test.setTimeout(150_000);
  await page.request.get("/api/demo/session");
  const warmPortfolio = await warmRead(page, "/api/invoices?view=open&page=1&page_size=25&sort=due&direction=asc");
  const warmData = await warmPortfolio.json();
  const warmInvoice = warmData.items?.[0];
  if (warmInvoice) {
    await Promise.all([
      page.request.get("/api/action-queue"),
      page.request.get(`/api/invoices/${encodeURIComponent(warmInvoice.id)}`),
      page.request.get(`/api/facts/clients/${encodeURIComponent(warmInvoice.client_id)}`),
      page.request.get("/api/clients?options_only=true&q="),
    ]);
  }
  const slowReads: string[] = [];
  const errors: string[] = [];
  const requests = new Map<string, number>();
  page.on("pageerror", error => errors.push(error.message));
  page.on("response", async response => {
    const request = response.request();
    const path = new URL(response.url()).pathname;
    if (request.method() !== "GET" || !path.startsWith("/api/")) return;
    await response.finished();
    const timing = request.timing();
    if (timing.responseEnd > 6000) slowReads.push(`${path}: ${timing.responseEnd}ms`);
    requests.set(path, (requests.get(path) || 0) + 1);
  });
  await page.goto("/#invoices");
  await expect(page.locator("#invoice-grid")).toBeVisible({ timeout: 10_000 });
  const invoice = page.locator('.ag-cell a[href^="/invoices/"]').first();
  const invoiceUrl = await invoice.getAttribute("href");
  const clientUrl = await page.locator('.ag-cell a[href^="/clients/"]').first().getAttribute("href");
  expect(invoiceUrl).toBeTruthy();
  expect(clientUrl).toBeTruthy();
  await page.getByLabel("Sort invoices").selectOption("predicted:asc");
  await expect(page).toHaveURL(/sort=predicted/);
  await page.getByLabel("Due from").fill("2026-01-01");
  await expect(page).toHaveURL(/due_from=2026-01-01/);
  await page.getByLabel("Search clients").focus();
  await expect(page.locator("#client-options")).toBeVisible();
  await page.getByLabel("Search clients").press("Escape");
  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Action queue" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh queue" })).toBeEnabled();
  await page.goto(clientUrl!);
  await expect(page.locator("h1")).toHaveText(/\S+/);
  await expect(page.getByLabel("Client invoice status")).toBeVisible();
  await page.goto(invoiceUrl!);
  await expect(page.getByText("Payment timing forecast", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy invoice link" })).toBeVisible();
  const invoiceId = new URL(invoiceUrl!, "http://localhost").pathname.split("/").pop();
  const facts = await (await page.request.get(`/api/invoices/${invoiceId}`)).json();
  const dueDate = new Date(`${facts.due_date}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC", month: "short", day: "numeric", year: "numeric" });
  await expect(page.locator(".fact").filter({ has: page.getByText("Due date", { exact: true }) }).getByText(dueDate, { exact: true })).toBeVisible();
  await page.goto("/invoices/does-not-exist");
  await expect(page.getByRole("heading", { name: "Invoice not found" })).toBeVisible();
  await page.waitForTimeout(1200);
  expect(requests.get("/api/invoices/does-not-exist")).toBe(1);
  expect(requests.get("/api/auth/me")).toBeLessThanOrEqual(5); // One provider mount per full navigation.
  expect(errors).toEqual([]);
  expect(slowReads).toEqual([]);
});

test("an editable synthetic invoice draft survives a reload", async ({ page }) => {
  const invoiceId = process.env.E2E_EDITABLE_INVOICE;
  test.skip(!invoiceId, "Set E2E_EDITABLE_INVOICE to a controlled synthetic reminder fixture.");
  await page.goto(`/invoices/${encodeURIComponent(invoiceId!)}`);
  await expect(page.getByText("Synthetic demo", { exact: true })).toBeVisible();
  const editor = page.getByRole("textbox", { name: "Editable reminder preview" });
  await expect(editor).toBeEditable();
  const original = await editor.inputValue();
  const facts = await (await page.request.get(`/api/invoices/${encodeURIComponent(invoiceId!)}`)).json();
  const recommendationId = facts.recommendation.id;
  const edited = `${original}\nReviewed draft persistence check.`;
  try {
    await editor.fill(edited);
    await Promise.all([page.waitForResponse(response => response.url().includes(`/recommendations/${recommendationId}/draft`) && response.status() === 200), editor.press("Tab")]);
    const saved = await (await page.request.get(`/api/invoices/${encodeURIComponent(invoiceId!)}`)).json();
    expect(saved.recommendation.edited_text).toBe(edited);
    await page.reload();
    await expect(editor).toHaveValue(edited);
  } finally {
    await page.request.post(`/api/recommendations/${recommendationId}/draft`, { data: { text: original } });
  }
});

test("cash-week cards open probability contributors with individual expected amounts", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/#cash");
  const weekCard = page.locator("#cash-forecast button.cash-week").first();
  await expect(weekCard).toBeVisible({ timeout: 90_000 });
  const filteredResponse = page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname === "/api/portfolio" && url.searchParams.has("predicted_week");
  });
  await weekCard.click();
  const response = await filteredResponse;
  expect(response.ok()).toBeTruthy();
  const result = await response.json();
  expect(result.items.length).toBeGreaterThan(0);
  const first = result.items[0];
  expect(first.cash_week_contribution).toBeTruthy();

  const contributorPanel = page.locator("#cash-contributors");
  await expect(contributorPanel).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("button", { name: /Settlement week:/ })).toBeVisible();
  const firstRow = contributorPanel.locator("tbody tr").first();
  await expect(firstRow).toBeVisible();
  await expect(firstRow.locator("td").nth(0).getByRole("link")).toHaveText(first.invoice_number);
  const amount = Number(first.cash_week_contribution.amount).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  await expect(firstRow.locator("td").nth(4)).toContainText(`${first.currency} ${amount}`);
});

test("portfolio action count matches the queue before opening the queue", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/");
  const summary = page.locator(".decision-hero-action strong");
  await expect(summary).toBeVisible({ timeout: 60_000 });
  const shownCount = Number((await summary.innerText()).match(/\d+/)?.[0]);
  const queue = await page.evaluate(async () => {
    const response = await fetch("/api/action-queue", { cache: "no-store" });
    if (!response.ok) throw new Error(`Queue returned HTTP ${response.status}`);
    return response.json() as Promise<{ pending_count: number }>;
  });
  expect(shownCount).toBe(queue.pending_count);
});

test("empty search labels filtered summaries and leaves client signals portfolio-wide", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/?q=no-such-demo-invoice-987654");
  await expect(page.getByText(/No matching invoices\./).first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole("tab", { name: "Cash forecast" }).click();
  await expect(page.getByText(/Filtered open balance by forecast horizon/)).toBeVisible();
  await page.getByRole("tab", { name: /Client signals/ }).click();
  await expect(page.getByText("Portfolio-wide client payment timing", { exact: true })).toBeVisible();
});

test("client invoice table preserves probabilities below one percent", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/#invoices");
  const clientHref = await page.locator('.ag-cell a[href^="/clients/"]').first().getAttribute("href");
  expect(clientHref).toBeTruthy();
  const clientId = new URL(clientHref!, "http://localhost").pathname.split("/").pop()!;
  const response = await page.request.get(`/api/facts/clients/${encodeURIComponent(clientId)}`);
  expect(response.ok()).toBeTruthy();
  const facts = await response.json();
  const target = facts.invoices.find((invoice: { forecast?: { probabilities?: Record<string, number> } | null }) => invoice.forecast?.probabilities?.["14d"] != null);
  expect(target).toBeTruthy();
  await page.route(`**/api/facts/clients/${clientId}`, async route => {
    const upstream = await route.fetch();
    const payload = await upstream.json();
    const invoice = payload.invoices.find((item: { id: string }) => item.id === target.id);
    invoice.forecast.probabilities["14d"] = 0.004;
    await route.fulfill({ response: upstream, body: JSON.stringify(payload) });
  });
  const separator = clientHref!.includes("?") ? "&" : "?";
  await page.goto(`${clientHref}${separator}page_size=100`);
  const row = page.locator(".grid-panel tbody tr").filter({ hasText: target.invoice_number });
  await expect(row.locator("td").nth(6)).toHaveText("<1%");
});

test("queue explains an empty successful response", async ({ page }) => {
  await page.goto("/actions");
  await page.route("**/api/action-queue", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({
      items: [], count: 0, daily_cap: 5, as_of: "2026-11-11", synthetic_demo: true,
      pending_count: 0, skipped_count: 0, skipped: [], collections_review: [], collections_totals: {},
    }),
  }));
  await page.getByRole("button", { name: "Refresh queue" }).click();
  await expect(page.getByText(/No reminder or partial-payment action is pending for this snapshot/)).toBeVisible();
});

test("queue reports the waking state and stops retrying after a 30-second API outage", async ({ page }) => {
  test.setTimeout(45_000);
  await page.route("**/api/action-queue", route => route.fulfill({
    status: 500,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Synthetic API outage for queue recovery test." }),
  }));
  await page.goto("/actions");
  await expect(page.getByRole("status").getByText(/Waking the API/)).toBeVisible({ timeout: 5_000 });
  await expect(page.getByRole("alert").filter({ hasText: /Could not refresh the action queue after repeated service checks/i })).toBeVisible({ timeout: 37_000 });
  await expect(page.getByRole("button", { name: "Refresh queue" })).toBeEnabled();
});

test("queue recovers from a controlled cold-start read failure", async ({ page }) => {
  test.setTimeout(90_000);
  let attempts = 0;
  await page.route("**/api/action-queue", async route => {
    attempts += 1;
    if (attempts === 1) {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Controlled cold-start response." }) });
      return;
    }
    await route.continue();
  });
  await page.goto("/actions");
  await expect(page.locator("#action-approval .queue-card").first()).toBeVisible({ timeout: 60_000 });
  expect(attempts).toBeGreaterThanOrEqual(2);
  await expect(page.getByRole("button", { name: "Refresh queue" })).toBeEnabled();
});

test("reply approval timeout stays blocked until Check status reconciles the pending preview", async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto("/actions");
  const invoiceHref = await page.locator("#action-approval a[href^='/invoices/']").first().getAttribute("href");
  expect(invoiceHref).toBeTruthy();
  const invoiceId = new URL(invoiceHref!, "http://localhost").pathname.split("/").pop()!;
  const triageId = "reply-timeout-ui-regression";
  let statusChecks = 0;
  const pendingPreview = { id: triageId, reply_text: "We will pay Friday.", classification: "promise_to_pay", classifier: "rules", promised_payment_date: "2026-11-13", state: "pending", created_at: "2026-11-12T10:00:00Z", reviewed_at: null };
  await page.route(`**/api/invoices/${invoiceId}`, async route => {
    const upstream = await route.fetch();
    const invoice = await upstream.json();
    invoice.reply_triages = [pendingPreview, ...(invoice.reply_triages || []).filter((item: { id: string }) => item.id !== triageId)];
    await route.fulfill({ response: upstream, body: JSON.stringify(invoice) });
  });
  await page.route(url => url.pathname.endsWith(`/api/invoices/${invoiceId}/reply`), route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ id: triageId, reply_text: "We will pay Friday.", classification: "promise_to_pay", classifier: "rules", promised_payment_date: "2026-11-13", state: "pending", reminders_paused: false, note: "Preview only." }),
  }));
  await page.route(url => url.pathname.endsWith(`/api/invoices/${invoiceId}/reply-triage/${triageId}/apply`), route => route.abort("timedout"));
  await page.route(`**/api/invoices/${invoiceId}/reply-triage/${triageId}`, async route => {
    if (route.request().method() !== "GET") { await route.continue(); return; }
    statusChecks += 1;
    if (statusChecks === 1) {
      await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Controlled status read interruption." }) });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ id: triageId, state: "pending", classification: "promise_to_pay", promised_payment_date: "2026-11-13", disputed: false, invoice_promised_payment_date: null, reviewed_at: null }),
    });
  });

  await page.goto(invoiceHref!);
  const reply = page.getByRole("textbox", { name: "Paste client reply" });
  await reply.fill("We will pay Friday.");
  await page.getByRole("button", { name: "Classify reply" }).click();
  await expect(page.locator(".reply-review")).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve classification" });
  await approve.click();
  await expect(page.getByText(/Use Check status before approving again/)).toBeVisible();
  await expect(approve).toBeDisabled();
  await page.reload();
  await expect(page.locator(".reply-review")).toBeVisible();
  const recoveredApprove = page.getByRole("button", { name: "Approve classification" });
  await expect(page.getByText(/The last reply review timed out and its saved state could not be confirmed/)).toBeVisible();
  await expect(recoveredApprove).toBeDisabled();
  await page.getByRole("button", { name: "Check status" }).click();
  await expect(page.getByText(/Status check confirms this preview is still pending/)).toBeVisible();
  await expect(recoveredApprove).toBeEnabled();
  expect(statusChecks).toBe(2);
});

test("guided tour exposes five stops and sends a failed live story to the packaged fallback", async ({ page }) => {
  await page.route("**/api/demo/session", route => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.route("**/api/demo-stories", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({ items: [
      { key: "verdant_outlier", title: "Verdant Market · stale outlier", summary: "Overdue age is a lower bound.", href: "/invoices/verdant-test" },
      { key: "dispute", title: "Cinder & Co · disputed invoice", summary: "A dispute pauses reminders.", href: "/invoices/dispute-test" },
    ] }),
  }));
  await page.goto("/tour?stop=1");
  const titles = ["Portfolio", "Verdant Market outlier", "Action queue", "Model performance", "Reply triage"];
  const navigation = page.locator(".tour-navigation");
  await expect(navigation).toHaveCSS("display", "flex");
  await expect(page.locator(".tour-progress")).toHaveCSS("display", "flex");
  for (let index = 0; index < titles.length; index += 1) {
    await expect(page.getByRole("heading", { name: titles[index], exact: true })).toBeVisible();
    const packagedFallback = page.getByRole("link", { name: `Open packaged fallback · stop ${index + 1}`, exact: true });
    await expect(packagedFallback).toHaveAttribute("href", `/offline-demo?stop=${index + 1}`);
    await expect(page.locator(".guided-stop > .eyebrow")).toContainText("about 20 seconds");
    if (index < titles.length - 1) await page.getByRole("button", { name: "Next stop" }).click();
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(navigation).toHaveCSS("display", "grid");
  const mobileProgress = await page.locator(".tour-progress").boundingBox();
  const mobilePrevious = await navigation.locator(":scope > button").first().boundingBox();
  const mobileNext = await navigation.locator(":scope > button").last().boundingBox();
  expect(mobileProgress && mobilePrevious && mobileNext).toBeTruthy();
  expect(mobileProgress!.y).toBeLessThan(mobilePrevious!.y);
  expect(Math.abs(mobilePrevious!.y - mobileNext!.y)).toBeLessThan(2);

  await page.route("**/api/**", route => route.abort());
  await page.goto("/");
  await expect(page.locator(".topbar")).toBeVisible();
  await expect(page.locator(".topbar .brand")).toHaveCSS("white-space", "nowrap");
  await expect(page.locator(".topbar .top-actions")).toHaveCSS("flex-wrap", "nowrap");
  await expect(page.locator(".topbar .top-actions")).toHaveCSS("overflow-x", "auto");
  const brandBox = await page.locator(".topbar .brand").boundingBox();
  const actionsBox = await page.locator(".topbar .top-actions").boundingBox();
  expect(brandBox && actionsBox).toBeTruthy();
  expect(actionsBox!.y).toBeGreaterThan(brandBox!.y);

  await page.route("**/api/demo-stories", route => route.abort());
  await page.goto("/tour?stop=2");
  await expect(page.getByRole("status").getByText(/Named story lookup failed/)).toBeVisible();
  const fallback = page.getByRole("link", { name: /Open local fallback/ });
  await expect(fallback).toBeVisible();
  await expect(fallback).toHaveAttribute("href", /\/offline-demo\?stop=2/);
});

test("packaged fallback opens its dated invoice evidence without API reads", async ({ page }) => {
  await page.route("**/api/**", route => route.abort());
  await page.goto("/offline-demo?stop=5");
  await expect(page.getByText(/Packaged snapshot/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Reply triage", exact: true })).toBeVisible();
  await expect(page.locator(".panel").first().getByText(/as of 2026-/i)).toBeVisible();
  const evidence = page.locator(".fallback-invoice-detail");
  await evidence.locator("summary").click();
  await expect(evidence.locator(".fallback-evidence-grid")).toBeVisible();
  await expect(evidence.getByText(/Outstanding/)).toBeVisible();
});

test("successful collections save stays distinct from a failed follow-up refresh", async ({ page }) => {
  test.setTimeout(60_000);
  let failQueueReads = false;
  const record = {
    id: "collection-refresh-fixture", invoice_number: "LP-REFRESH-01", client_name: "Refresh Test",
    client_id: "refresh-client", source: "demo", currency: "USD", outstanding: "100.00",
    due_date: "2026-01-01", days_late: 315,
    decision: { action: "review_for_writeoff_or_collections", reason: "Long-overdue balance", expected_cash_gain: 0 },
    why: "Review this balance.", suggested_review_steps: [], recommendation: null,
    collection_review: { owner: "", notes: "", follow_up_date: null, resolution_status: "open", updated_at: null },
  };
  const queue = {
    items: [], count: 0, daily_cap: 5, as_of: "2026-11-11", synthetic_demo: true,
    pending_count: 0, skipped_count: 0, skipped: [], collections_review: [record],
    collections_closed: [], collections_totals: { USD: "100.00" },
  };
  await page.route("**/api/action-queue", route => failQueueReads
    ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Controlled refresh interruption." }) })
    : route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(queue) }));
  await page.route("**/collection-review", route => route.request().method() === "PATCH"
    ? route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ saved: true }) })
    : route.continue());
  await page.goto("/collections");
  const card = page.locator(".queue-card").first();
  await expect(card).toBeVisible({ timeout: 60_000 });
  await card.getByLabel("Owner").fill("Refresh failure regression");
  failQueueReads = true;
  await card.getByRole("button", { name: "Save review" }).click();
  await expect(card.getByRole("status").filter({ hasText: "Saved, but refresh failed." })).toBeVisible({ timeout: 45_000 });
  await expect(card.getByRole("button", { name: "Check status" })).toBeVisible();
  await expect(card.getByRole("button", { name: "Retry refresh" })).toBeVisible();
});

test("collections save survives reload and refreshes from the saved record", async ({ page }) => {
  const record = {
    id: "collection-ui-invoice", invoice_number: "LP-UI-123", client_name: "Collections Demo",
    client_id: "collection-ui-client", source: "demo", currency: "USD", outstanding: "100.00",
    due_date: "2026-01-01", days_late: 300,
    decision: { action: "review_for_writeoff_or_collections", reason: "Long-overdue balance", expected_cash_gain: 0 },
    why: "Review this balance.", suggested_review_steps: [], recommendation: null,
    collection_review: { owner: "", notes: "", follow_up_date: null as string | null, resolution_status: "open", updated_at: null as string | null },
  };
  const response = () => ({
    items: [], count: 0, daily_cap: 5, as_of: "2026-11-11", synthetic_demo: true,
    pending_count: 0, skipped_count: 0, skipped: [], collections_review: [record],
    collections_closed: [], collections_totals: { USD: "100.00" },
  });
  await page.route("**/api/demo/session", route => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.route("**/api/action-queue", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response()) }));
  await page.route("**/api/invoices/collection-ui-invoice/collection-review", async route => {
    const draft = route.request().postDataJSON() as { owner: string; notes: string; follow_up_date: string | null; resolution_status: string };
    record.collection_review = { ...draft, updated_at: "2026-11-12T10:00:00Z" };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ invoice_id: record.id, ...record.collection_review }) });
  });

  await page.goto("/collections");
  await expect(page.getByRole("heading", { name: "Collections", exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Owner" }).fill("A. Reviewer");
  await page.getByLabel("Review notes").fill("Customer requested a corrected statement.");
  await page.getByRole("button", { name: "Save review" }).click();
  await expect(page.getByRole("status").getByText(/Saved ·/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "Owner" })).toHaveValue("A. Reviewer");
  await expect(page.getByLabel("Review notes")).toHaveValue("Customer requested a corrected statement.");
  await expect(page.getByText(/Last saved/)).toBeVisible();
});

test("demo reset opens an accessible confirmation and cancel or Escape keeps the session", async ({ page }) => {
  test.setTimeout(90_000);
  let resetRequests = 0;
  const emptyPortfolio = {
    items: [], count: 0, page: 1, page_size: 25, page_count: 1, view: "open",
    view_counts: { open: 0, due_this_week: 0, overdue: 0, paid: 0, all: 0 },
    currency_totals: {},
    aging_buckets: Object.fromEntries(["not_due", "late_1_30", "late_31_60", "late_61_89", "late_90_plus"].map(key => [key, { count: 0, amounts: {} }])),
    expected_cash_by_week: [], cash_horizon: {},
    top_late_scenario: { assumption: "10% illustrative scenario", client_count: 0, clients: [], invoice_count: 0, baseline_by_currency: [], cash_by_week: [] },
    synthetic_demo: true, models: [], ready_for_review_count: 0, daily_suggestion_cap: 5, drifting_client_count: 0,
    habitually_late_client_count: 0, drifting_signal_counts: {}, as_of: "2026-11-12", as_of_max: "2026-11-12", model_timestamp: null, client_signals: [],
  };
  await page.route("**/api/portfolio*", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(emptyPortfolio) }));
  await page.route("**/api/demo/session/reset", route => {
    resetRequests += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.goto("/");
  const resetButton = page.getByRole("button", { name: "Reset demo" });
  await expect(resetButton).toBeVisible({ timeout: 60_000 });
  await resetButton.click();
  const dialog = page.getByRole("dialog", { name: "Reset this browser’s demo session?" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Simulated payments, replies, approvals, promises, and disputes");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(resetButton).toBeFocused();
  expect(resetRequests).toBe(0);
  await resetButton.click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  expect(resetRequests).toBe(0);
});

test("bulk and per-invoice PayPal approvals show reviewable dialogs before any write", async ({ page }) => {
  test.setTimeout(90_000);
  const recommendation = (id: string) => ({
    id: `recommendation-${id}`, action: "friendly", tone: "friendly", edited_text: `Internal note for ${id}.`,
    draft_text: `Internal note for ${id}.`, draft_source: "template", reason: "Low probability of payment by the due date.", state: "proposed",
  });
  const item = (id: string, number: string, client: string) => ({
    id, invoice_number: number, client_name: client, client_id: `client-${id}`, client_email: `${client.toLowerCase().replaceAll(" ", "")}@example.test`,
    source: "paypal", currency: "USD", outstanding: "1250.00", due_date: "2026-11-15", days_late: 0,
    decision: { action: "friendly_reminder", reason: "Due soon.", expected_cash_gain: 125 },
    recommendation: recommendation(id),
  });
  const queue = {
    items: [item("invoice-a", "LP-REVIEW-101", "Acme Review"), item("invoice-b", "LP-REVIEW-102", "Birch Review")],
    count: 2, daily_cap: 1, as_of: "2026-11-12", synthetic_demo: false,
    pending_count: 2, skipped_count: 0, skipped: [], collections_review: [], collections_closed: [], collections_totals: {},
  };
  let providerWrites = 0;
  await page.route("**/api/action-queue", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(queue) }));
  await page.route("**/api/recommendations/**", route => {
    if (route.request().method() !== "GET") providerWrites += 1;
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ state: "sent" }) });
  });
  await page.goto("/actions");
  await expect(page.getByRole("heading", { name: "Action queue" })).toBeVisible();

  await page.getByRole("button", { name: "Approve eligible friendly reminders" }).click();
  const bulkDialog = page.getByRole("dialog", { name: "Review friendly reminders before approval" });
  await expect(bulkDialog).toBeVisible();
  await expect(bulkDialog).toContainText("2 reminders are eligible; 1 are included under the shared cap of 1");
  await expect(bulkDialog).toContainText("LP-REVIEW-101 · Acme Review");
  await expect(bulkDialog).not.toContainText("LP-REVIEW-102");
  await bulkDialog.getByRole("button", { name: "Cancel" }).click();
  expect(providerWrites).toBe(0);

  await page.locator(".queue-card").first().getByRole("button", { name: "Review PayPal reminder" }).click();
  const providerDialog = page.getByRole("dialog", { name: "Approve this PayPal Sandbox reminder?" });
  await expect(providerDialog).toBeVisible();
  await expect(providerDialog).toContainText("PayPal sends its system generated reminder email");
  await expect(providerDialog).toContainText("Recipient acmereview@example.test");
  await expect(providerDialog).toContainText("Assumed 14-day cash uplift (10% scenario): USD 125.00");
  await providerDialog.getByRole("button", { name: "Cancel" }).click();
  expect(providerWrites).toBe(0);
});
