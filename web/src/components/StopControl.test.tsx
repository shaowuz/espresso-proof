import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Header } from "./Header";
import type { StatusView } from "../lib/types";

const base: StatusView = {
  state: "LITERATURE_DONE",
  proof_status: "idea_sketch",
  completed: false,
  routes_preliminary: false,
  selected_route: null,
  evidence: { reference_supported_steps: 0, machine_checks: 0 },
  budget: {},
  agents: [],
};

describe("stop control", () => {
  it("shows what is running and offers to stop it", () => {
    const html = renderToStaticMarkup(
      <Header
        claim={undefined}
        status={{
          ...base,
          active_job: { job_id: "j1", action: "auto_continue", state: "running" },
        }}
      />,
    );
    expect(html).toContain("continuing");
    expect(html).toContain('aria-label="Stop the running job"');
  });

  it("shows no stop control when the run is idle", () => {
    const html = renderToStaticMarkup(
      <Header claim={undefined} status={{ ...base, active_job: null }} />,
    );
    expect(html).not.toContain("Stop the running job");
  });
});
