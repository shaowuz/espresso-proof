/**
 * Goal 55 browser/product latency validation: the instant conversation lane.
 *
 * Proves, against the REAL cockpit page, that a deliberately SLOW workflow does
 * not block or blank the instant draft:
 *   - run creation is delayed by ESPRESSO_TEST_CREATE_DELAY_MS (set by the runner);
 *   - the page acknowledges fast and shows a labeled "Draft only — not checked"
 *     draft well under 3s — long before the slow run finishes;
 *   - the draft is neutral (never earned-green) and the page is never blank.
 *
 * Requires a running cockpit at COCKPIT_URL (the runner starts it with the slow
 * create hook). Fail-closed: any missing assertion exits non-zero.
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const base = process.env.COCKPIT_URL ?? "http://127.0.0.1:8447";
const out = process.env.SHOT_DIR ?? "/tmp/g55_latency_shots";
mkdirSync(out, { recursive: true });
const exe = join(
  homedir(),
  "Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
);

const ACK_BUDGET_MS = 1500; // generous CI-safe bound on first visible ack
const DRAFT_BUDGET_MS = 3000; // the goal's hard <3s draft/fallback budget

const failures = [];
const log = (ok, msg) => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${msg}`);
  if (!ok) failures.push(msg);
};

const browser = await chromium.launch({ executablePath: exe, headless: true });
const consoleErrors = [];
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(500);

// submit a fresh claim through the real bottom composer.
const composer = page.locator("textarea").first();
await composer.fill("Show that every group of order p^2 is abelian.");

const t0 = Date.now();
await page.getByLabel("Send command to Espresso").click();

// first visible acknowledgement: the instant-draft panel appears with its label.
let ackMs = -1;
for (let i = 0; i < 60; i++) {
  const visible = await page.locator("text=Draft only — not checked").first().isVisible().catch(() => false);
  if (visible) {
    ackMs = Date.now() - t0;
    break;
  }
  await page.waitForTimeout(50);
}
log(ackMs >= 0, "instant-draft panel acknowledged");
log(ackMs >= 0 && ackMs < ACK_BUDGET_MS, `first visible ack under ${ACK_BUDGET_MS}ms (got ${ackMs}ms)`);

// first useful draft text — must arrive well under 3s even though run creation is
// artificially delayed by the slow-create hook.
let draftMs = -1;
for (let i = 0; i < 80; i++) {
  const text = await page.evaluate(() => document.body.innerText);
  if (/Route\s*1:|restate the claim precisely|initial read while/i.test(text)) {
    draftMs = Date.now() - t0;
    break;
  }
  await page.waitForTimeout(50);
}
log(draftMs >= 0, "instant draft text appeared");
log(draftMs >= 0 && draftMs < DRAFT_BUDGET_MS, `first draft text under ${DRAFT_BUDGET_MS}ms (got ${draftMs}ms)`);

// the page is NOT blank and the full read is still pending (slow run not done):
// the "Preparing the full read" placeholder proves the draft did not wait for
// the slow workflow.
const bodyMid = await page.evaluate(() => document.body.innerText);
log(/Preparing the full read/i.test(bodyMid), "page shows the instant lane while the slow run is still creating (not blank, did not wait)");

// the draft is neutral — never the earned evidence-green.
const green = await page.evaluate(
  () => document.querySelectorAll(".bg-green-50,.bg-green-100,.text-green-700,.text-green-800").length,
);
log(green === 0, `no earned-green styling on the draft (got ${green})`);

// no proof-complete / earned-status language leaked into the draft.
log(
  !/\bPROOF-CLOSE\b|is proved|proof is complete|source_backed/i.test(bodyMid),
  "draft contains no proof-complete / source-backed language",
);

log(consoleErrors.length === 0, `no console errors (got ${consoleErrors.length}${consoleErrors[0] ? ": " + consoleErrors[0] : ""})`);
await page.screenshot({ path: join(out, "instant_draft.png"), fullPage: false });
await browser.close();
console.log(
  failures.length === 0
    ? "\n55 LATENCY VALIDATION: ALL PASS"
    : `\n55 LATENCY VALIDATION: ${failures.length} FAILURE(S)`,
);
process.exit(failures.length === 0 ? 0 : 1);
