/**
 * Goal 43C browser validation: rating controls + two ledgers + latest-round
 * ledger, driven via playwright-core against the cached Chromium. Fail-closed
 * (non-zero exit on any failure). The feedback controls and any "saved" state
 * must NEVER be green (a score is not evidence).
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const base = process.env.COCKPIT_URL ?? "http://127.0.0.1:8433";
const out = process.env.SHOT_DIR ?? "/tmp/g43c_shots";
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

// expand the first strategy card to reveal rating controls + ledgers.
const stratToggle = page
  .locator("section button[aria-expanded]")
  .filter({ hasNotText: /raw input/i })
  .first();
log((await stratToggle.count()) > 0, "a strategy card is present");
await stratToggle.click();
await page.waitForTimeout(300);

const body = () => page.evaluate(() => document.body.innerText.toLowerCase());
let txt = await body();
log(txt.includes("advisory"), "rating block shows the advisory label");
log(
  txt.includes("affects how the next round") || txt.includes("not current evidence"),
  "advisory copy: feedback affects next round, not current evidence",
);
log(txt.includes("evidence ledger") || txt.includes("source gaps"), "evidence ledger rendered");
log(
  txt.includes("time allocation") || txt.includes("attention") || txt.includes("your score"),
  "time-allocation ledger rendered",
);

// the rating controls exist: priority buttons + a score control + save.
const priorityBtns = await page.getByRole("button", { name: /focus|reduce|repair/i }).count();
log(priorityBtns >= 1, `priority buttons present (got ${priorityBtns})`);
const saveBtn = page.getByRole("button", { name: /save/i }).first();
log((await saveBtn.count()) > 0, "save-feedback button present");

// drive a feedback save and confirm it persists (my_feedback summary appears).
const range = page.locator('input[type="range"]').first();
let targetScore = "88";
if ((await range.count()) > 0) {
  const currentScore = await range.inputValue();
  targetScore = currentScore === "88" ? "73" : "88";
  await range.fill(targetScore);
}
const focusBtn = page.getByRole("button", { name: /focus/i }).first();
if ((await focusBtn.count()) > 0) await focusBtn.click();
await saveBtn.click();
await page.waitForTimeout(2500); // allow POST + panel invalidation/refetch
txt = await body();
log(txt.includes(targetScore) || txt.includes("saved"), "feedback save reflected in UI (score/saved)");

// kernel-safety: no earned-green badge anywhere, and the rating/saved chips
// are not green (a score is not evidence).
const green = await page.evaluate(
  () => document.querySelectorAll(".bg-green-50,.bg-green-100,.text-green-700,.text-green-800").length,
);
log(green === 0, `no green status styling on an unearned run incl. feedback (got ${green})`);
log(consoleErrors.length === 0, `no console errors (got ${consoleErrors.length}${consoleErrors[0] ? ": " + consoleErrors[0] : ""})`);

await page.screenshot({ path: join(out, "feedback.png"), fullPage: false });

// trigger a continue and confirm the latest-round ledger appears.
const continueBtn = page.getByRole("button", { name: /continue this route/i }).first();
if ((await continueBtn.count()) > 0) {
  await continueBtn.click();
  const settingsBtn = page.getByRole("button", { name: /run details and settings/i }).first();
  if ((await settingsBtn.count()) > 0) {
    await settingsBtn.click();
  }
  // wait for the round to finish and the settings ledger to render (poll up to ~40s).
  let sawLedger = false;
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(1000);
    const t = await body();
    if (t.includes("latest round") || (t.includes("subgoal graph changed") || t.includes("stop reason") || t.includes("rounds"))) {
      sawLedger = true;
      break;
    }
  }
  log(sawLedger, "latest-round ledger appears after a continue");
  await page.screenshot({ path: join(out, "round_ledger.png"), fullPage: false });
} else {
  log(false, "continue button present to trigger the round ledger");
}

await browser.close();
console.log(failures.length === 0 ? "\n43C VALIDATION: ALL PASS" : `\n43C VALIDATION: ${failures.length} FAILURE(S)`);
process.exit(failures.length === 0 ? 0 : 1);
