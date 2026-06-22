import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AgentFlowTrace } from "./AgentFlowTrace";
import type { TracePanel } from "../lib/types";

const trace: TracePanel = {
  run_id: "run_1",
  panel: "trace",
  missing: false,
  agent_state: "ready",
  unavailable: false,
  advisory_note: "Stage state means process progress only.",
  items: [
    {
      stage_id: "strategy_grader",
      display_name: "Strategy Grader",
      state_word: "Stage finished",
      tone: "muted",
      group: "main",
      evidence_role_word: "Advisory only",
      advisory_note: "Process trace only; not a proof or evidence signal.",
      blocked_reason: "",
      status_detail: "Waiting for source locator lane to finish.",
      updated_at: "2026-06-20T00:00:00Z",
    },
    {
      stage_id: "literature_review",
      display_name: "Literature Review",
      state_word: "Blocked",
      tone: "warn",
      group: "pressure lanes",
      evidence_role_word: "Candidate context only",
      advisory_note: "Process trace only; not a proof or evidence signal.",
      blocked_reason: "Waiting for claim-clear gate.",
      status_detail: "Waiting for claim-clear gate.",
      updated_at: "2026-06-20T00:00:00Z",
    },
  ],
};

describe("AgentFlowTrace", () => {
  it("is collapsed by default so the compact workspace remains first", () => {
    const html = renderToStaticMarkup(
      <AgentFlowTrace trace={trace} isPending={false} isError={false} />,
    );

    expect(html).toContain("Agent flow trace");
    expect(html).toContain("Collapsed by default");
    expect(html).toContain("2 stages");
    expect(html).not.toContain("Stage finished");
    expect(html).not.toContain("Strategy Grader");
  });

  it("renders expanded process wording without proof or earned-green styling", () => {
    const html = renderToStaticMarkup(
      <AgentFlowTrace trace={trace} isPending={false} isError={false} defaultOpen />,
    );
    const lower = html.toLowerCase();

    expect(html).toContain("Stage finished");
    expect(html).toContain("Process trace only");
    expect(html).toContain("Waiting for source locator lane to finish.");
    expect(html).toContain("Waiting for claim-clear gate.");
    for (const forbidden of [
      "proof complete",
      "we proved",
      "verified",
      "source_backed",
      "artifact_hashes",
      "raw yaml",
      "traceback",
      "bg-green",
      "text-green",
    ]) {
      expect(lower).not.toContain(forbidden);
    }
  });

  it("shows unavailable trace as advisory, not evidence", () => {
    const html = renderToStaticMarkup(
      <AgentFlowTrace
        trace={{ ...trace, missing: true, unavailable: true, items: [] }}
        isPending={false}
        isError
        errorText="Process trace unavailable; no mathematical status changed."
        defaultOpen
      />,
    );

    expect(html).toContain("Process trace unavailable");
    expect(html).not.toContain("bg-green");
    expect(html).not.toContain("text-green");
  });
});
