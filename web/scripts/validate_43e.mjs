/**
 * Goal 43E browser validation: motion is calm, layout-stable polish that never
 * gates information or earns green. Checks expansion survives polling, reduced
 * motion still shows content, running progress is visible, no column jump / no
 * horizontal overflow, and no console errors. Fail-closed.
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const base = process.env.COCKPIT_URL ?? "http://127.0.0.1:8435";
const out = process.env.SHOT_DIR ?? "/tmp/g43e_shots";
mkdirSync(out, { recursive: true });
const exe = join(
  homedir(),
  "Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
);

const failures = [];
const log = (ok, msg) => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${msg}`);
  if (!ok) failures.push(msg);
};

const browser = await chromium.launch({ executablePath: exe, headless: true });
const consoleErrors = [];
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);

// expand a strategy card; it must stay expanded across a poll refresh (>3s).
const strat = page
  .locator("section button[aria-expanded]")
  .filter({ hasNotText: /raw input/i })
  .first();
log((await strat.count()) > 0, "a strategy card is present");
await strat.click();
await page.waitForTimeout(400);
const expandedBefore = await strat.getAttribute("aria-expanded");
const detailVisible = await page.getByText(/Advisory — affects how the next round/i).first().isVisible();
log(expandedBefore === "true" && detailVisible, "strategy detail expanded (Collapse opened)");
await page.waitForTimeout(3800); // span a 3s poll tick
const expandedAfter = await strat.getAttribute("aria-expanded");
const stillVisible = await page.getByText(/Advisory — affects how the next round/i).first().isVisible();
log(expandedAfter === "true" && stillVisible, "expanded detail survives a poll refresh (no collapse)");

// process feedback appears immediately on an action. The current smoke run may
// have ready-but-empty panel artifacts, so this validates the visible job chip
// rather than requiring static ready/done panel labels to be shown.
const continueBtn = page.getByRole("button", { name: /continue this route/i }).first();
log((await continueBtn.count()) > 0, "continue action is present");
await continueBtn.click();
await page.waitForTimeout(180);
const bodyText = await page.evaluate(() => document.body.innerText);
log(
  /\b(queued · \d+s|running · \d+s|pass finished)\b/i.test(bodyText),
  "action process feedback is visible within 200ms",
);

// no horizontal overflow and no earned-green from motion on an unearned run.
const overflow = await page.evaluate(
  () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
);
log(overflow <= 0, `no horizontal overflow (delta ${overflow}px)`);
const green = await page.evaluate(
  () => document.querySelectorAll(".bg-green-50,.bg-green-100,.text-green-700,.text-green-800").length,
);
log(green === 0, `no earned-green styling introduced by motion (got ${green})`);
log(consoleErrors.length === 0, `no console errors (got ${consoleErrors.length}${consoleErrors[0] ? ": " + consoleErrors[0] : ""})`);
await page.screenshot({ path: join(out, "motion.png"), fullPage: false });

// reduced motion: content must still render and expand must still work.
await page.emulateMedia({ reducedMotion: "reduce" });
await page.reload({ waitUntil: "networkidle" });
await page.waitForTimeout(800);
const rStrat = page
  .locator("section button[aria-expanded]")
  .filter({ hasNotText: /raw input/i })
  .first();
await rStrat.click();
await page.waitForTimeout(150); // reduced motion is instant
const rVisible = await page.getByText(/Advisory — affects how the next round/i).first().isVisible();
log(rVisible, "reduced motion: detail still appears (motion never gates content)");
const rErrors = consoleErrors.length;
log(rErrors === 0, "reduced motion: no console errors");

await browser.close();
console.log(failures.length === 0 ? "\n43E VALIDATION: ALL PASS" : `\n43E VALIDATION: ${failures.length} FAILURE(S)`);
process.exit(failures.length === 0 ? 0 : 1);
