import { test, expect } from "@playwright/test";

const enabled = process.env.E2E_PAYPAL_SANDBOX === "1";
const username = process.env.E2E_PAYPAL_USERNAME;
const password = process.env.E2E_PAYPAL_PASSWORD;

test("operator creates an allowlisted Sandbox invoice through the browser proxy", async ({ page }) => {
  test.skip(!enabled, "Set E2E_PAYPAL_SANDBOX=1 to create a real PayPal Sandbox invoice.");
  test.skip(!username || !password, "Set E2E_PAYPAL_USERNAME and E2E_PAYPAL_PASSWORD for the Sandbox operator.");

  await page.goto("/login");
  await page.getByLabel("Username").fill(username!);
  await page.getByLabel("Password").fill(password!);
  await Promise.all([
    page.waitForURL("**/"),
    page.getByRole("button", { name: "Sign in" }).click(),
  ]);

  const sandboxPanel = page.locator("details").filter({ hasText: "PayPal Sandbox · controlled invoice and sync workflow" });
  await sandboxPanel.locator("summary").click();
  const recipient = sandboxPanel.getByLabel("Sandbox invoice recipient");
  await expect.poll(() => recipient.locator("option").count(), { timeout: 30_000 }).toBeGreaterThan(1);
  await recipient.selectOption({ index: 1 });
  await sandboxPanel.getByLabel("Invoice amount").fill("1.23");
  await sandboxPanel.getByLabel("Payment terms").selectOption("NET_15");

  const createResponse = page.waitForResponse(response =>
    response.url().endsWith("/api/paypal/invoices") && response.request().method() === "POST",
  );
  await sandboxPanel.getByRole("button", { name: "Create & send Sandbox invoice" }).click();
  const response = await createResponse;
  expect(response.status()).toBe(200);
  const idempotencyKey = response.request().headers()["idempotency-key"];
  expect(idempotencyKey).toMatch(/^[A-Za-z0-9_-]{32,64}$/);

  const created = await response.json() as { invoice_id: string; invoice_number: string; status: string };
  expect(created.invoice_id).toBeTruthy();
  expect(created.invoice_number).toBeTruthy();
  expect(created.status).toBe("SENT");
  await expect(sandboxPanel.getByRole("status")).toContainText(created.invoice_number);

  const invoice = await (await page.request.get(`/api/invoices/${encodeURIComponent(created.invoice_id)}`)).json();
  expect(invoice.source).toBe("paypal");
  expect(invoice.paypal_id).toBeTruthy();
  expect(invoice.invoice_number).toBe(created.invoice_number);
});
