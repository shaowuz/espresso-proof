import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { StrategyCard } from "./Workspace";
import type { StrategyView } from "../lib/types";

const strategy: StrategyView = {
  id: "strategy_route_001",
  title: "Pigeonhole on the left-multiplication map",
  one_line: "",
  score: 50,
  status_sentence: "",
  source_phrase: "candidate only",
  source_tone: "neutral",
  main_risk: "",
  next_step: "",
  selected: false,
  my_feedback: null,
  allowed_efforts: ["quick_test"],
};

function render(continueAvailable: boolean): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>
      <StrategyCard
        strategy={strategy}
        runId="r1"
        onChanged={() => {}}
        activeObject={null}
        onSetActiveObject={() => {}}
        continueAvailable={continueAvailable}
      />
    </QueryClientProvider>,
  );
}

describe("StrategyCard continue gating", () => {
  it("disables Continue with an explanation before the decision point", () => {
    // Before the run reaches the prover human gate the kernel rejects a
    // continue; the button must not offer a click that can only fail.
    const html = render(false);
    expect(html).toContain("Unlocks at your decision point");
    expect(html).toContain("The initial pass is still running");
  });

  it("keeps Continue live at or past the decision point", () => {
    const html = render(true);
    expect(html).toContain("Continue this route");
    expect(html).not.toContain("Unlocks at your decision point");
  });
});
