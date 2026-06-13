/**
 * Goal 43F: chat-stream client. The stream is a conversational PREVIEW —
 * never evidence. It is read over a POST + Server-Sent-Events body; every line
 * was already proof-language-scrubbed server-side. If streaming fails for any
 * reason the caller falls back to polling/artifacts, so the cockpit always
 * works with streaming disabled.
 */

export interface SSEEvent {
  event: string;
  data: { text?: string; ok?: boolean };
}

/**
 * Pure incremental SSE parser: given the accumulated buffer, return the
 * complete events parsed so far plus the unconsumed remainder. An event is a
 * block terminated by a blank line ("\n\n").
 */
export function parseSSE(buffer: string): { events: SSEEvent[]; rest: string } {
  const events: SSEEvent[] = [];
  let rest = buffer;
  let idx: number;
  while ((idx = rest.indexOf("\n\n")) !== -1) {
    const block = rest.slice(0, idx);
    rest = rest.slice(idx + 2);
    let event = "message";
    let dataRaw = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataRaw += line.slice(5).trim();
    }
    if (dataRaw !== "") {
      try {
        events.push({ event, data: JSON.parse(dataRaw) });
      } catch {
        /* skip a malformed block; the stream is only a preview */
      }
    }
  }
  return { events, rest };
}

/**
 * Stream the conversational preview for a run. Calls `onLine` per assistant
 * line and `onDone(ok)` at the end. Any transport error resolves through
 * `onDone(false)` — never throws — so the UI can show its fallback.
 */
export async function streamChat(
  runId: string,
  message: string,
  onLine: (text: string) => void,
  onDone: (ok: boolean) => void,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/runs/${encodeURIComponent(runId)}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
  } catch {
    onDone(false);
    return;
  }
  if (!res.ok || res.body === null) {
    onDone(false);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let ok = false;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parsed = parseSSE(buffer);
      buffer = parsed.rest;
      for (const ev of parsed.events) {
        if (ev.event === "message" && typeof ev.data.text === "string") onLine(ev.data.text);
        else if (ev.event === "done") ok = Boolean(ev.data.ok);
      }
    }
  } catch {
    onDone(false);
    return;
  }
  onDone(ok);
}
