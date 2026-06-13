import { describe, expect, it } from "vitest";
import { parseSSE } from "./chat";

describe("parseSSE", () => {
  it("parses complete events and keeps the unconsumed remainder", () => {
    const buf =
      'event: message\ndata: {"text":"hello"}\n\n' +
      'event: message\ndata: {"text":"world"}\n\n' +
      "event: done\ndata: {";
    const { events, rest } = parseSSE(buf);
    expect(events).toEqual([
      { event: "message", data: { text: "hello" } },
      { event: "message", data: { text: "world" } },
    ]);
    expect(rest).toBe("event: done\ndata: {");
  });

  it("defaults the event name to message and reassembles across chunks", () => {
    // a half-arrived event is not emitted until its blank-line terminator.
    let { events, rest } = parseSSE('data: {"text":"a"}');
    expect(events).toEqual([]);
    ({ events, rest } = parseSSE(rest + '\n\n'));
    expect(events).toEqual([{ event: "message", data: { text: "a" } }]);
  });

  it("skips a malformed data block without throwing (preview only)", () => {
    const { events } = parseSSE("event: message\ndata: not-json\n\n");
    expect(events).toEqual([]);
  });

  it("parses a done event with ok flag", () => {
    const { events } = parseSSE("event: done\ndata: {\"ok\":true}\n\n");
    expect(events).toEqual([{ event: "done", data: { ok: true } }]);
  });
});
