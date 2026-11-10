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
