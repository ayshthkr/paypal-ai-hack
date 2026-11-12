import { chromium } from "@playwright/test";

const baseUrl = (process.env.DEMO_TOUR_BASE_URL || "https://latepayer-web.onrender.com").replace(/\/$/, "");
const budgetMs = 180_000;
const browser = await chromium.launch({ headless: true, channel: process.env.DEMO_TOUR_BROWSER_CHANNEL || "chromium" });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, timezoneId: "Asia/Kolkata" });
const page = await context.newPage();
page.setDefaultTimeout(25_000);
page.setDefaultNavigationTimeout(45_000);
const started = Date.now();
const results = [];
const pageErrors = [];
page.on("pageerror", error => pageErrors.push(error.message));

function remaining() {
  const value = budgetMs - (Date.now() - started);
  if (value <= 0) throw new Error("The five-stop route exceeded its 180-second budget.");
  return value;
}

async function openAndWait(stop, liveLabel, ready, fallbackReady) {
  await page.goto(`${baseUrl}/tour?stop=${stop}`, { waitUntil: "domcontentloaded", timeout: Math.min(remaining(), 45_000) });
  await page.getByRole("heading", { name: "Guided demo tour" }).waitFor({ state: "visible", timeout: Math.min(remaining(), 30_000) });
  const live = page.getByRole("link", { name: liveLabel, exact: false }).first();
  const target = liveLabel === "Open live story" ? page.getByRole("link", { name: /Open live|Open local fallback/ }).first() : live;
  await target.waitFor({ state: "visible", timeout: Math.min(remaining(), 24_000) });
  await target.click();
  const routeStarted = Date.now();
  let mode = await fallbackReady.isVisible().catch(() => false) ? "fallback" : null;
  if (!mode) {
    try {
      await ready.waitFor({ state: "visible", timeout: Math.min(remaining(), 24_000) });
      mode = "live";
    } catch { /* Use the on-page packaged fallback after its bounded live read. */ }
  }
  if (!mode) {
    const inlineFallback = page.getByRole("link", { name: "Open fallback demo", exact: true });
    const sharedFallback = page.getByRole("link", { name: "Read-only fallback", exact: true }).last();
    if (await inlineFallback.isVisible().catch(() => false)) await inlineFallback.click();
    else if (await sharedFallback.isVisible().catch(() => false)) await sharedFallback.click();
    else throw new Error(`Stop ${stop} did not render live content or expose its packaged fallback.`);
    const stopButton = page.getByRole("button", { name: `Offline stop ${stop}` });
    if (stop > 1 && await stopButton.isVisible().catch(() => false)) await stopButton.click();
    await fallbackReady.waitFor({ state: "visible", timeout: Math.min(remaining(), 15_000) });
    mode = "fallback";
  }
  if (mode === "live" && stop < 5) {
    const continueTour = page.getByRole("link", { name: "Continue tour →", exact: true });
    await continueTour.waitFor({ state: "visible", timeout: Math.min(remaining(), 5_000) });
    await continueTour.click();
    await page.getByRole("heading", { name: "Guided demo tour" }).waitFor({ state: "visible", timeout: Math.min(remaining(), 5_000) });
  }
  results.push({ stop, mode, seconds: Math.round((Date.now() - routeStarted) / 100) / 10 });
}

try {
  await openAndWait(1, "Open live Portfolio", page.locator("#invoice-grid"), page.getByText(/Packaged snapshot/));
  await openAndWait(2, "Open live story", page.getByText("Payment timing forecast", { exact: true }), page.getByText(/Offline stop 2 of 5/));
  await openAndWait(3, "Open live Action queue", page.locator("#action-approval .queue-card").first(), page.getByText(/Offline stop 3 of 5/));
  await openAndWait(4, "Open live Model performance", page.locator(".performance-section-nav"), page.getByText(/Offline stop 4 of 5/));
  await openAndWait(5, "Open live story", page.locator("#reply-triage"), page.getByText(/Offline stop 5 of 5/));
  const elapsedSeconds = Math.round((Date.now() - started) / 100) / 10;
  if (pageErrors.length) throw new Error(`Browser errors during the tour: ${pageErrors.join("; ")}`);
  if (elapsedSeconds * 1000 > budgetMs) throw new Error(`Route took ${elapsedSeconds}s, over the 180-second budget.`);
  console.log(JSON.stringify({ baseUrl, elapsedSeconds, budgetSeconds: budgetMs / 1000, stops: results }, null, 2));
} finally {
  await browser.close();
}
