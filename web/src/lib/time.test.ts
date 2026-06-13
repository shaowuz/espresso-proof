import { describe, expect, it } from "vitest";
import { formatDuration, timeAgo } from "./time";

describe("formatDuration", () => {
  it("formats sub-minute, minute, and hour ranges", () => {
    expect(formatDuration(0)).toBe("0s");
    expect(formatDuration(3.9)).toBe("3s"); // floored
    expect(formatDuration(59)).toBe("59s");
    expect(formatDuration(60)).toBe("1m 00s");
    expect(formatDuration(64)).toBe("1m 04s");
    expect(formatDuration(3600)).toBe("1h 00m");
    expect(formatDuration(3725)).toBe("1h 02m");
  });

  it("never goes negative", () => {
    expect(formatDuration(-5)).toBe("0s");
  });
});

describe("timeAgo", () => {
  const now = Date.parse("2026-06-13T12:00:00Z");
  it("renders relative phrases", () => {
    expect(timeAgo("2026-06-13T11:59:59Z", now)).toBe("just now");
    expect(timeAgo("2026-06-13T11:59:50Z", now)).toBe("10s ago");
    expect(timeAgo("2026-06-13T11:58:00Z", now)).toBe("2m ago");
    expect(timeAgo("2026-06-13T10:00:00Z", now)).toBe("2h ago");
  });

  it("returns empty for missing or unparseable input", () => {
    expect(timeAgo(null, now)).toBe("");
    expect(timeAgo("", now)).toBe("");
    expect(timeAgo("not-a-date", now)).toBe("");
  });

  it("clamps a future timestamp to just now (never negative)", () => {
    expect(timeAgo("2026-06-13T12:00:30Z", now)).toBe("just now");
  });
});
