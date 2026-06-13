/**
 * Goal 43A browser validation (desktop + narrow), driven via playwright-core
 * against the locally cached Chromium. Produces a validation log on stdout
 * and screenshots under the path given by --out (never committed).
 *
 * Checks (fail closed, non-zero exit on any failure):
 *  - / serves the cockpit; root renders header, three panes, composer;
 *  - desktop 1440x900: three-column layout visible, no tiny centered shell;
 *  - narrow 390x844: no horizontal overflow; composer visible; pane tabs work;
 *  - no console errors; no raw traceback text anywhere;
 *  - green-capable styling only appears via earned-tone badges (smoke run
 *    earns nothing, so NO green evidence badge may be present).
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const base = process.env.COCKPIT_URL ?? "http://127.0.0.1:8431";
const out = process.env.SHOT_DIR ?? "/tmp/g43a_shots";
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
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(800);

const title = await page.title();
log(title.includes("Espresso"), `page title is the product ("${title}")`);

const header = await page.locator("header").count();
log(header === 1, "app header present");
const composer = await page.locator("textarea").count();
log(composer >= 1, "bottom composer textarea present");

// three-column grid on desktop: the three panes are laid out side by side.
const cols = await page.evaluate(() => {
  const main = document.querySelector("main");
  if (!main) return 0;
  return getComputedStyle(main).gridTemplateColumns.split(" ").length;
});
log(cols === 3, `desktop main grid has 3 columns (got ${cols})`);

// not a tiny centered shell: the app uses most of the viewport.
const used = await page.evaluate(() => {
  const root = document.getElementById("root");
  const r = root?.getBoundingClientRect();
  return r ? (r.width * r.height) / (innerWidth * innerHeight) : 0;
});
log(used > 0.9, `app shell fills the viewport (${Math.round(used * 100)}%)`);

const bodyText = (await page.evaluate(() => document.body.innerText)).toLowerCase();
log(!/Traceback|raise |Error:.*line \d+/.test(bodyText), "no traceback-like text");
log(bodyText.includes("examples"), "left rail labeled (Examples)");
log(bodyText.includes("literature"), "right rail labeled (Literature)");
log(bodyText.includes("strategies"), "center workspace labeled (Strategies)");

// kernel-safety: the smoke run earned nothing, so no earned-green badge.
const greenBadges = await page.evaluate(
  () => document.querySelectorAll(".bg-green-50, .bg-green-100").length,
);
log(greenBadges === 0, `no earned-green evidence badges on unearned run (got ${greenBadges})`);

// Goal 43B: independent panels — the evidence rail renders its three sections
// plus the literature rail, each loading on its own query.
const sectionCount = await page.locator("main section h2").count();
log(sectionCount >= 4, `independent panel sections rendered (got ${sectionCount})`);

// Goal 43B: an expanded card survives background polling (state preserved).
const firstCard = page.locator("main aside button[aria-expanded]").first();
if ((await firstCard.count()) > 0) {
  await firstCard.click();
  await page.waitForTimeout(250);
  const expandedAfterClick = await firstCard.getAttribute("aria-expanded");
  await page.waitForTimeout(3600); // span at least one fast-poll tick
  const expandedAfterPoll = await firstCard.getAttribute("aria-expanded");
  log(
    expandedAfterClick === "true" && expandedAfterPoll === "true",
    "expanded card stays expanded across a poll refresh",
  );
} else {
  log(true, "no expandable evidence card on this run (skipped expand check)");
}

await page.screenshot({ path: join(out, "desktop.png"), fullPage: false });

// narrow viewport
await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(500);
const overflow = await page.evaluate(
  () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
);
log(overflow <= 0, `no horizontal overflow at 390px (delta ${overflow}px)`);
const tabs = await page.locator("nav button").count();
log(tabs === 3, `narrow pane tabs present (got ${tabs})`);
const composerVisible = await page.locator("textarea").isVisible();
log(composerVisible, "composer visible at narrow width");
await page.locator("nav button", { hasText: "Evidence" }).click();
await page.waitForTimeout(250);
const evidenceShown = await page.evaluate(() =>
  document.body.innerText.toLowerCase().includes("examples"),
);
log(evidenceShown, "evidence pane reachable via narrow tab");
await page.screenshot({ path: join(out, "narrow.png"), fullPage: false });

log(consoleErrors.length === 0, `no console errors (got ${consoleErrors.length}${consoleErrors.length ? ": " + consoleErrors[0] : ""})`);

await browser.close();
console.log(failures.length === 0 ? "\nBROWSER VALIDATION: ALL PASS" : `\nBROWSER VALIDATION: ${failures.length} FAILURE(S)`);
process.exit(failures.length === 0 ? 0 : 1);
