/**
 * Browser validation for Goals 55/56/57:
 * - clean claim summary hides hostile raw input until the raw panel opens;
 * - clicking a strategy creates an active route context across workspace,
 *   rails, composer, and chat preview;
 * - workflow polish states remain neutral and do not introduce earned green.
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const base = process.env.COCKPIT_URL ?? "http://127.0.0.1:8441";
const out = process.env.SHOT_DIR ?? "/tmp/g55_56_57_shots";
mkdirSync(out, { recursive: true });

const exe = join(
  homedir(),
  "Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
);

const CLAIM =
  "Show that $|G| = p^2$; hide <script>alert('xss')</script> and " +
  "\\href{javascript:alert('xss')}{bad link} before deeper strategy work.";

const failures = [];
const log = (ok, msg) => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${msg}`);
  if (!ok) failures.push(msg);
};

async function poll(path, predicate, ms = 30000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    const r = await fetch(base + path).then((x) => x.json()).catch(() => null);
    const v = r && predicate(r);
    if (v) return v;
    await new Promise((res) => setTimeout(res, 400));
  }
  return null;
}

const created = await fetch(base + "/api/runs", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text: CLAIM }),
}).then((r) => r.json());
const runId = await poll(
  `/api/jobs/${created.job_id}`,
  (job) => (job.state === "done" ? job.result.run_id : null),
);
log(Boolean(runId), `created hostile-input run (${runId ?? "FAILED"})`);

const browser = await chromium.launch({ executablePath: exe, headless: true });
const consoleErrors = [];
let alertFired = false;
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("dialog", async (dialog) => {
  alertFired = true;
  await dialog.dismiss();
});

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

let body = await page.evaluate(() => document.body.innerText);
log(!body.toLowerCase().includes("<script"), "main viewport hides raw <script> text");
log(!body.toLowerCase().includes("javascript:"), "main viewport hides javascript: text");
log(!body.includes("\\href"), "main viewport hides raw href command");
log(await page.getByText("Show raw input").first().isVisible(), "raw input is collapsed behind a control");
log(await page.getByText("Unsafe or malformed markup was hidden").first().isVisible(), "claim warning copy visible");

await page.getByText("Show raw input").first().click();
await page.waitForTimeout(200);
body = await page.evaluate(() => document.body.innerText);
log(body.includes("<script>alert('xss')</script>"), "raw input is inspectable after opening");
log(body.includes("javascript:alert"), "raw JavaScript URL is preserved as text after opening");

const danger = await page.evaluate(() => {
  const scripts = document.querySelectorAll("main script, header script, section script").length;
  const jsHref = Array.from(document.querySelectorAll("a[href]")).some((a) =>
    (a.getAttribute("href") || "").toLowerCase().startsWith("javascript:"),
  );
  return { scripts, jsHref };
});
log(!alertFired, "raw claim did not execute alert()");
log(danger.scripts === 0, `no injected script nodes (got ${danger.scripts})`);
log(danger.jsHref === false, "no javascript: links created");

const firstStrategy = page.locator("main section button[aria-expanded]").first();
log((await firstStrategy.count()) > 0, "strategy card is available");
await firstStrategy.click();
await page.waitForTimeout(350);
log(await page.getByText("Active route").first().isVisible(), "clicking strategy shows Active route");
log(
  (await page.locator("textarea").first().getAttribute("placeholder")).includes("Ask about active strategy"),
  "composer placeholder targets active strategy",
);
log(await page.getByText(/No route linkage recorded yet|Related examples appear first/i).first().isVisible(), "evidence rail reacts to active route");
log(await page.getByText(/Source linkage is not route-specific yet|Related literature appears first/i).first().isVisible(), "literature rail reacts to active route");

await page.locator("textarea").first().fill("what blocks this active route?");
await page.locator('button[aria-label="Send command to Espresso"]').click();
await page.waitForTimeout(500);
const conversation = await page.getByLabel("Conversation preview").innerText();
log(/Context requested: active strategy/i.test(conversation), "chat preview records active strategy context");
log(/what blocks this active route/i.test(conversation), "chat preview records the user message");

await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
const overflow = await page.evaluate(
  () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
);
log(overflow <= 0, `no horizontal overflow at narrow width (delta ${overflow}px)`);
log(await page.locator("textarea").first().isVisible(), "composer remains reachable at narrow width");

const green = await page.evaluate(
  () => document.querySelectorAll(".bg-green-50,.bg-green-100,.text-green-700,.text-green-800").length,
);
log(green === 0, `active/raw/chat states add no earned-green styling (got ${green})`);
log(consoleErrors.length === 0, `no console errors (got ${consoleErrors.length}${consoleErrors[0] ? ": " + consoleErrors[0] : ""})`);

await page.screenshot({ path: join(out, "active-raw-chat.png"), fullPage: false });
await browser.close();
console.log(failures.length === 0 ? "\n55/56/57 VALIDATION: ALL PASS" : `\n55/56/57 VALIDATION: ${failures.length} FAILURE(S)`);
process.exit(failures.length === 0 ? 0 : 1);
