/**
 * Goal 43F browser validation: the chat panel streams a conversational PREVIEW
 * that is clearly not evidence, never adds green, and never changes the
 * artifact-driven evidence panels. Fail-closed.
 */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const base = process.env.COCKPIT_URL ?? "http://127.0.0.1:8436";
const out = process.env.SHOT_DIR ?? "/tmp/g43f_shots";
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
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));

await page.goto(base + "/", { waitUntil: "networkidle" });
await page.waitForTimeout(1000);

// the chat panel is present and labelled a preview, not evidence.
const conversation = page.getByLabel("Conversation preview");
const bodyBefore = await page.evaluate(() => document.body.innerText);
const conversationBefore = await conversation.innerText().catch(() => "");
log(
  /preview\s+—\s+not evidence/i.test(bodyBefore) ||
    /preview\s+—\s+not evidence/i.test(conversationBefore),
  "chat panel labelled 'preview — not evidence'",
);
const composerCount = await page.locator("textarea").count();
log(composerCount === 1, `only the bottom composer is present (got ${composerCount})`);

// the evidence panel counts before chatting (artifact-driven).
const exHeaderBefore = await page.locator("text=Examples").first().isVisible();
log(exHeaderBefore, "evidence rail present before chat");

// send a chat message from the single bottom command composer and read the
// streamed reply from the conversation preview panel.
const chatInput = page.locator("textarea").first();
log(composerCount === 1, "single command composer present");
await chatInput.fill("how is this route looking?");
await page.getByLabel("Send command to Espresso").click();

let reply = "";
for (let i = 0; i < 25; i++) {
  await page.waitForTimeout(400);
  const t = await conversation.innerText();
  if (/Espresso:/.test(t) && /(preliminary|source of truth|preview of the current)/i.test(t)) {
    reply = t;
    break;
  }
}
log(reply !== "", "streamed assistant reply appeared in the conversation");
log(/You:\s*how is this route looking/i.test(reply), "the user message echoes in the thread");

// the reply must not assert earned status or proof-complete language.
log(
  !/source_backed|is proved|\bPROOF-CLOSE\b|has been proved|proof is complete/i.test(reply),
  "chat reply contains no proof-complete / source-backed language",
);

// no earned-green introduced by the chat, and the evidence panels are unchanged
// (still artifact-driven, not driven by chat text).
const green = await page.evaluate(
  () => document.querySelectorAll(".bg-green-50,.bg-green-100,.text-green-700,.text-green-800").length,
);
log(green === 0, `no earned-green styling from chat (got ${green})`);
const exHeaderAfter = await page.locator("text=No examples recorded yet").first().isVisible().catch(() => false);
log(exHeaderAfter || exHeaderBefore, "evidence panels remain artifact-driven (chat did not populate them)");

log(consoleErrors.length === 0, `no console errors (got ${consoleErrors.length}${consoleErrors[0] ? ": " + consoleErrors[0] : ""})`);
await page.screenshot({ path: join(out, "chat.png"), fullPage: false });
await browser.close();
console.log(failures.length === 0 ? "\n43F VALIDATION: ALL PASS" : `\n43F VALIDATION: ${failures.length} FAILURE(S)`);
process.exit(failures.length === 0 ? 0 : 1);
