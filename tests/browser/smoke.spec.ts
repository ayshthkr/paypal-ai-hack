import { test, expect } from "@playwright/test";

test("portfolio, queue, client and invoice workflow stays within warm budgets", async ({ page }) => {
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
    if (timing.responseEnd > 2000) slowReads.push(`${path}: ${timing.responseEnd}ms`);
    requests.set(path, (requests.get(path) || 0) + 1);
  });
  await page.goto("/");
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
  await page.goto("/");
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
  await expect(page.getByText(/Filtered open balance by forecast horizon/)).toBeVisible();
  await expect(page.getByText("Portfolio-wide client payment timing", { exact: true })).toBeVisible();
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
  await page.goto("/actions");
  await page.route("**/api/action-queue", route => route.fulfill({
    status: 500,
    contentType: "application/json",
    body: JSON.stringify({ detail: "Synthetic API outage for queue recovery test." }),
  }));
  await page.getByRole("button", { name: "Refresh queue" }).click();
  await expect(page.getByRole("status").getByText(/Waking the API/)).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText(/Could not load the action queue/)).toBeVisible({ timeout: 37_000 });
  await expect(page.getByRole("button", { name: "Refresh queue" })).toBeEnabled();
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
  for (let index = 0; index < titles.length; index += 1) {
    await expect(page.getByRole("heading", { name: titles[index], exact: true })).toBeVisible();
    if (index < titles.length - 1) await page.getByRole("button", { name: "Next stop" }).click();
  }

  await page.route("**/api/demo-stories", route => route.abort());
  await page.goto("/tour?stop=2");
  await expect(page.getByRole("status").getByText(/Named story lookup failed/)).toBeVisible();
  const fallback = page.getByRole("link", { name: /Open local fallback/ });
  await expect(fallback).toBeVisible();
  await expect(fallback).toHaveAttribute("href", /\/offline-demo\?stop=2/);
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
