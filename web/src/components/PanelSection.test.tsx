import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AgentStateChip, PanelSection, PanelSkeleton } from "./PanelSection";
import type { AgentState } from "../lib/types";

describe("AgentStateChip", () => {
  it("renders neutral process states without earned evidence styling", () => {
    const states: AgentState[] = ["queued", "running", "done", "failed", "skipped", "not_run"];
    for (const state of states) {
      const html = renderToStaticMarkup(<AgentStateChip state={state} />);
      expect(html).toContain(state === "not_run" ? "not run" : state === "failed" ? "agent failed" : state);
      expect(html).not.toContain("bg-green");
      expect(html).not.toContain("text-green");
      expect(html).not.toContain("earned");
    }
  });

  it("does not render a chip for ready state", () => {
    expect(renderToStaticMarkup(<AgentStateChip state="ready" />)).toBe("");
  });
});

describe("PanelSkeleton", () => {
  it("renders card-shaped neutral placeholders", () => {
    const html = renderToStaticMarkup(<PanelSkeleton rows={2} />);
    expect(html).toContain("Panel skeleton");
    expect(html).toContain("bg-stone");
    expect(html).not.toContain("bg-green");
  });
});

describe("PanelSection", () => {
  it("shows a skeleton while a panel is running before data exists", () => {
    const html = renderToStaticMarkup(
      <PanelSection
        title="Examples"
        agentState="running"
        isPending={false}
        isError={false}
        hasData={false}
        emptyText="No examples yet"
      >
        <span>existing content</span>
      </PanelSection>,
    );
    expect(html).toContain("Panel skeleton");
    expect(html).not.toContain("No examples yet");
    expect(html).not.toContain("existing content");
  });

  it("keeps existing data visible while a panel refreshes", () => {
    const html = renderToStaticMarkup(
      <PanelSection
        title="Examples"
        agentState="running"
        isPending={false}
        isRefetching
        isError={false}
        hasData
        emptyText="No examples yet"
      >
        <span>existing content</span>
      </PanelSection>,
    );
    expect(html).toContain("Panel skeleton");
    expect(html).toContain("existing content");
    expect(html).not.toContain("No examples yet");
  });

  it("surfaces elapsed process detail without green evidence styling", () => {
    const html = renderToStaticMarkup(
      <PanelSection
        title="Examples"
        agentState="done"
        agent={{
          agent: "examples",
          title: "Examples",
          status: "done",
          status_word: "done",
          last_updated: null,
          duration_seconds: 3.2,
        }}
        isPending={false}
        isError={false}
        hasData
        emptyText="No examples yet"
      >
        <span>existing content</span>
      </PanelSection>,
    );
    expect(html).toContain("done");
    expect(html).toContain("3s");
    expect(html).not.toContain("bg-green");
  });

  it("renders pressure lane evidence notes as process-only context", () => {
    const html = renderToStaticMarkup(
      <PanelSection
        title="Examples"
        agentState="not_run"
        pressureLane={{
          stage_id: "examples",
          agent: "examples",
          title: "Examples",
          display_name: "Examples",
          status: "not_started",
          status_word: "Not started",
          agent_status: "not_run",
          default_visible: true,
          default_execution: "on_demand",
          counts_as_evidence: false,
          pending_counts_as_evidence: false,
          evidence_note: "pending; not counted as evidence",
          claim_clear_blocked: false,
        }}
        isPending={false}
        isError={false}
        hasData={false}
        emptyText="No examples yet"
      />,
    );
    expect(html).toContain("Not started");
    expect(html).toContain("pending; not counted as evidence");
    expect(html).not.toContain("bg-green");
  });
});
