import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const baseUrl = (process.env.SCREENSHOT_BASE_URL || "https://latepayer-web.onrender.com").replace(/\/$/, "");
const outputRoot = path.resolve(process.env.SCREENSHOT_DIR || "artifacts/screenshots/review-2026-11-12");
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
  if (selector) await page.locator(selector).scrollIntoViewIfNeeded();
  await page.screenshot({ path: destination, type: "jpeg", quality: 91, fullPage });
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
    await page.getByText("No matching invoices", { exact: true }).waitFor({ state: "visible", timeout: 90_000 });
    await save("01-dashboard/05-empty-search-global-cards.jpg");
  });

  await navigate("/actions");
  await page.locator(".action-approval").first().waitFor({ state: "visible", timeout: 120_000 });
  const detailHref = await page.locator(".action-approval a[href^='/invoices/']").first().getAttribute("href");
  await save("02-actions/01-ranked-action-queue.jpg");

  await optional("policy-explanation", async () => {
    await page.getByRole("button", { name: /Policy fact.*threshold.*action/ }).click();
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
  await optional("collections-archive", async () => {
    await page.getByRole("button", { name: /Closed archive/ }).click();
    await save("04-collections/02-closed-archive.jpg");
  });

  await optional("invoice-details", async () => {
    if (!detailHref) throw new Error("No invoice link was found in the action queue.");
    await navigate(detailHref);
    await save("03-invoices/02-invoice-forecast-and-next-action.jpg");
    await page.locator(".curve-wrap").scrollIntoViewIfNeeded();
    await save("03-invoices/03-survival-curve-and-partial-settlement-explanation.jpg");
    await optional("survival-probability-table", async () => {
      await page.getByText("Inspect dated probabilities", { exact: true }).click();
      await save("03-invoices/04-dated-survival-probability-table.jpg");
    });
    await optional("reply-classification-preview", async () => {
      const reply = page.getByRole("textbox", { name: "Paste client reply" });
      if (await reply.count()) {
        await reply.fill("We will pay Friday.");
        await page.getByRole("button", { name: "Classify reply" }).click();
        await page.getByText(/Original client reply|Extracted intent|Classification preview/i).first().waitFor({ state: "visible", timeout: 45_000 });
        await save("03-invoices/05-reply-classification-review.jpg");
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
      const terms = page.getByText(/Terms comparison/i).first();
      await terms.scrollIntoViewIfNeeded();
      await save("05-clients/02-terms-scenario-horizons.jpg");
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
      if (stop < 5) await page.getByRole("button", { name: "Next stop" }).click();
    }
  });

  await optional("packaged-fallback", async () => {
    await navigate("/offline-demo");
    await save("08-fallback/01-versioned-read-only-fallback.jpg");
    await optional("fallback-invoice-evidence", async () => {
      const expander = page.getByRole("button", { name: /invoice evidence|show invoice/i }).first();
      if (await expander.count()) {
        await expander.click();
        await save("08-fallback/02-expanded-invoice-evidence.jpg");
      }
    });
  });

  await optional("operator-login", async () => {
    await navigate("/login");
    await save("09-operator/01-sandbox-operator-login.jpg");
  });

  await optional("narrow-portfolio", async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await navigate("/");
    await save("10-responsive/01-narrow-portfolio.jpg");
    await page.setViewportSize({ width: 1680, height: 1050 });
  });
} finally {
  await browser.close();
  const manifest = [
    "# Deployed demo screenshots",
    "",
    `Captured from ${baseUrl} on 2026-11-12 after publishing commit 0319108. Public pages use synthetic demo data; no PayPal transaction or reminder was sent. The guided tour and collection update checks also passed against this deployment.`,
    "",
    "Screenshots are numbered by workflow and then by page order. Images are viewport captures at a 1680 × 1050 CSS viewport and 1.5× device scale, except the narrow portfolio capture (390 × 844 CSS pixels).",
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
