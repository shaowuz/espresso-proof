import { afterEach, describe, expect, it, vi } from "vitest";
import { streamInstantDraft, streamInstantDraftTransient } from "./instantDraft";

function sseResponse(blocks: string[], status = 200): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const b of blocks) controller.enqueue(enc.encode(b));
      controller.close();
    },
  });
  return new Response(stream, { status });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("streamInstantDraft", () => {
  it("acks, streams draft lines, and reports the final status from metrics", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: ack\ndata: {"working":true}\n\n',
          'event: draft\ndata: {"text":"A first read of the claim."}\n\n',
          'event: draft\ndata: {"text":"Route 1: restate the claim precisely."}\n\n',
          'event: done\ndata: {"ok":true,"instant_lane_metrics":{"instant_lane_status":"fallback"},"advisory":true}\n\n',
        ]),
      ),
    );
    const lines: string[] = [];
    let acked = false;
    let done: { ok: boolean; status: string } | null = null;
    await streamInstantDraft("run-1", "is it true?", {
      onAck: () => {
        acked = true;
      },
      onLine: (t) => lines.push(t),
      onDone: (ok, status) => {
        done = { ok, status };
      },
    });
    expect(acked).toBe(true);
    expect(lines[0]).toBe("A first read of the claim.");
    expect(lines).toHaveLength(2);
    expect(done).toEqual({ ok: true, status: "fallback" });
  });

  it("resolves through onDone(false, 'failed') on a transport error (never throws)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    let done: { ok: boolean; status: string } | null = null;
    await streamInstantDraft("run-1", "x", {
      onLine: () => {},
      onDone: (ok, status) => {
        done = { ok, status };
      },
    });
    expect(done).toEqual({ ok: false, status: "failed" });
  });

  it("the transient variant posts to the pre-run endpoint and streams", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'event: ack\ndata: {"working":true}\n\n',
        'event: draft\ndata: {"text":"A first read of the claim."}\n\n',
        'event: done\ndata: {"ok":true,"instant_lane_metrics":{"instant_lane_status":"fallback"}}\n\n',
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const lines: string[] = [];
    let done: { ok: boolean; status: string } | null = null;
    await streamInstantDraftTransient("claim text", {
      onLine: (t) => lines.push(t),
      onDone: (ok, status) => {
        done = { ok, status };
      },
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/instant-draft");
    expect(lines).toEqual(["A first read of the claim."]);
    expect(done).toEqual({ ok: true, status: "fallback" });
  });

  it("treats a non-ok response as a failed stream", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("nope", { status: 403 })));
    let done: { ok: boolean; status: string } | null = null;
    await streamInstantDraft("run-1", "x", {
      onLine: () => {},
      onDone: (ok, status) => {
        done = { ok, status };
      },
    });
    expect(done).toEqual({ ok: false, status: "failed" });
  });
});
