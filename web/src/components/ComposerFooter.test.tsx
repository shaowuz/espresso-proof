import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Composer, EFFORTS } from "./Composer";
import { effortForRoute } from "./Workspace";

/**
 * The composer footer carries only controls that do something: the effort the
 * next route continuation runs at, and a send button. (The executor chip is
 * fetched from /api/info and so is absent in this static render.)
 */

function render(effort = "normal_pass") {
  return renderToStaticMarkup(
    <Composer
      runId={null}
      onRunCreated={() => undefined}
      onChatMessage={async () => undefined}
      chatBusy={false}
      activeObject={null}
      onClearActiveObject={() => undefined}
      effort={effort}
      onEffortChange={() => undefined}
    />,
  );
}

describe("composer footer", () => {
  it("offers every effort the kernel accepts, and no invented one", () => {
    const html = render();
    for (const option of EFFORTS) {
      expect(html).toContain(`value="${option.value}"`);
      expect(html).toContain(option.label);
    }
    expect(EFFORTS.map((e) => e.value)).toEqual([
      "quick_test",
      "normal_pass",
      "deep_exploration",
    ]);
  });

  it("keeps one labelled send control", () => {
    const html = render();
    expect(html).toContain('aria-label="Send command to Espresso"');
    expect((html.match(/aria-label="Send command to Espresso"/g) ?? []).length).toBe(1);
  });
});

describe("effortForRoute", () => {
  it("uses the chosen effort when the route allows it", () => {
    expect(effortForRoute("deep_exploration", ["normal_pass", "deep_exploration"])).toBe(
      "deep_exploration",
    );
  });

  it("falls back to the route's own effort rather than sending a rejected one", () => {
    // The lock-claim route is quick-only; sending "deep_exploration" would be
    // rewritten server-side and the UI would claim an effort never used.
    expect(effortForRoute("deep_exploration", ["quick_test"])).toBe("quick_test");
    expect(effortForRoute(undefined, ["quick_test"])).toBe("quick_test");
    expect(effortForRoute("deep_exploration", undefined)).toBeUndefined();
  });
});
