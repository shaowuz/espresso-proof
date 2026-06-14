/**
 * Goal 55: instant-draft client. The instant lane answers a claim FAST with a
 * labeled DRAFT ("Draft only — not checked") while the slow artifact/kernel
 * pipeline runs in the background. Like the chat stream, it is a PREVIEW —
 * never evidence; every line was already proof-language-scrubbed server-side.
 *
 * It reads a POST + Server-Sent-Events body with three event kinds:
 *   - `ack`   {label, working}              — emitted immediately
 *   - `draft` {text}                        — one already-scrubbed draft line
 *   - `done`  {ok, instant_lane_metrics, advisory}
 *
 * Any transport error resolves through `onDone(false, "failed")` — it never
 * throws — so the cockpit always works with the lane disabled.
 */

import { parseSSE } from "./chat";

export type InstantStatus = "draft" | "fallback" | "failed" | "unavailable";

export interface InstantDraftHandlers {
  onAck?: () => void;
  onLine: (text: string) => void;
  onDone: (ok: boolean, status: InstantStatus) => void;
}

export async function streamInstantDraft(
  runId: string,
  message: string,
  handlers: InstantDraftHandlers,
): Promise<void> {
  await consumeInstantDraft(
    `/api/runs/${encodeURIComponent(runId)}/instant-draft`,
    message,
    handlers,
  );
}

/**
 * Goal 55: the PRE-RUN instant lane. Answers the submitted claim text the moment
 * it is sent, decoupled from run creation, so a slow workflow can never blank the
 * page. Same contract as {@link streamInstantDraft} but with no run id.
 */
export async function streamInstantDraftTransient(
  message: string,
  handlers: InstantDraftHandlers,
): Promise<void> {
  await consumeInstantDraft("/api/instant-draft", message, handlers);
}

async function consumeInstantDraft(
  url: string,
  message: string,
  handlers: InstantDraftHandlers,
): Promise<void> {
  const { onAck, onLine, onDone } = handlers;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
  } catch {
    onDone(false, "failed");
    return;
  }
  if (!res.ok || res.body === null) {
    onDone(false, "failed");
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let ok = false;
  let status: InstantStatus = "failed";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSSE(buffer);
      buffer = parsed.rest;
      for (const ev of parsed.events) {
        const data = ev.data as unknown as Record<string, unknown>;
        if (ev.event === "ack") {
          onAck?.();
        } else if (ev.event === "draft" && typeof data.text === "string") {
          onLine(data.text);
        } else if (ev.event === "done") {
          ok = Boolean(data.ok);
          const metrics = data.instant_lane_metrics as
            | { instant_lane_status?: InstantStatus }
            | undefined;
          if (metrics?.instant_lane_status) status = metrics.instant_lane_status;
        }
      }
    }
  } catch {
    onDone(false, "failed");
    return;
  }
  onDone(ok, ok ? status : "failed");
}
