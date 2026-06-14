/**
 * Goal 43D browser validation: KaTeX renders inline + block math in the cockpit,
 * malformed TeX falls back to copyable text, and no math field can inject script
 * or a javascript: link. Fail-closed (non-zero exit on any failure).
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const base = process.env.COCKPIT_URL ?? "http://127.0.0.1:8434";
const out = process.env.SHOT_DIR ?? "/tmp/g43d_shots";
mkdirSync(out, { recursive: true });
const exe = join(
  homedir(),
  "Library/Caches/ms-playwright/chromium-1223/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
);

// a claim mixing inline math, block math, malformed TeX, and an injection
// attempt — the front-half preserves the normalized statement text.
const CLAIM =
  "Show that $|G| = p^2$ forces $G/Z(G)$ cyclic for $f: \\mathbb{R} \\to \\mathbb{R}$; " +
  "$$\\int_0^1 x\\,dx$$ note a bad $\\frac{$ and a link \\href{javascript:alert('xss')}{x} <script>alert('raw')</script>.";

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
    await new Promise((res) => setTimeout(res, 500));
  }
  return null;
}

// create the run via the API.
const created = await fetch(base + "/api/runs", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text: CLAIM }),
}).then((r) => r.json());
const runId = await poll(
  `/api/jobs/${created.job_id}`,
  (j) => (j.state === "done" ? j.result.run_id : null),
);
log(Boolean(runId), `created a run with TeX claim (${runId ?? "FAILED"})`);

const browser = await chromium.launch({ executablePath: exe, headless: true });
const consoleErrors = [];
let alertFired = false;
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("dialog", async (d) => {
  alertFired = true; // an injected alert() would open a dialog
  await d.dismiss();
});

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(1200);

// KaTeX rendered inline + block math somewhere on the page.
const katexCount = await page.locator(".katex").count();
log(katexCount >= 2, `KaTeX rendered math elements (got ${katexCount})`);
const blockCount = await page.locator(".katex-display").count();
log(blockCount >= 1, `block math rendered as display (got ${blockCount})`);

// the injection did NOT execute and no dangerous nodes were injected.
log(!alertFired, "no alert() dialog fired from the claim (no script execution)");
const danger = await page.evaluate(() => {
  const scripts = document.querySelectorAll("main script, header script, section script").length;
  const jsHref = Array.from(document.querySelectorAll("a[href]")).some((a) =>
    (a.getAttribute("href") || "").toLowerCase().startsWith("javascript:"),
  );
  return { scripts, jsHref };
});
log(danger.scripts === 0, `no injected <script> nodes in content (got ${danger.scripts})`);
log(danger.jsHref === false, "no javascript: href injected from the math field");

// Goal 57 refines 43D: malformed TeX is hidden from the main claim summary
// but remains copyable in the raw-input panel.
const bodyText = await page.evaluate(() => document.body.innerText);
log(
  /Malformed TeX hidden|Unsafe or malformed markup was hidden/i.test(bodyText),
  "malformed TeX is flagged without dominating the main claim card",
);
await page.getByText("Show raw input").first().click();
await page.waitForTimeout(200);
const rawBodyText = await page.evaluate(() => document.body.innerText);
log(rawBodyText.includes("\\frac{"), "malformed TeX remains readable in raw input");
log(!/Traceback|Cannot read|undefined is not/.test(bodyText), "no error text leaked into the page");

// the claim still reads (the surrounding prose is present as escaped text).
log(bodyText.includes("forces") && bodyText.includes("cyclic"), "claim prose renders around the math");

log(consoleErrors.length === 0, `no console errors (got ${consoleErrors.length}${consoleErrors[0] ? ": " + consoleErrors[0] : ""})`);

await page.screenshot({ path: join(out, "katex.png"), fullPage: false });
await browser.close();
console.log(failures.length === 0 ? "\n43D VALIDATION: ALL PASS" : `\n43D VALIDATION: ${failures.length} FAILURE(S)`);
process.exit(failures.length === 0 ? 0 : 1);
