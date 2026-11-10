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
  await page.goto("/invoices/does-not-exist");
  await expect(page.getByRole("heading", { name: "Invoice not found" })).toBeVisible();
  await page.waitForTimeout(1200);
  expect(requests.get("/api/invoices/does-not-exist")).toBe(1);
  expect(requests.get("/api/auth/me")).toBeLessThanOrEqual(5); // One provider mount per full navigation.
  expect(errors).toEqual([]);
  expect(slowReads).toEqual([]);
});
