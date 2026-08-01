import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { InstantDraft } from "./InstantDraft";

// renderToStaticMarkup does not run effects, so this asserts the SAFETY-critical
// initial render: the locked label, neutral (never earned-green) styling, the
// working state, and the superseded banner.

describe("InstantDraft", () => {
  it("renders nothing without a claim", () => {
    expect(renderToStaticMarkup(<InstantDraft claimText="" />)).toBe("");
  });

  it("shows the draft's own card and neutral styling, never earned-green", () => {
    const html = renderToStaticMarkup(
      <InstantDraft claimText="Show |G|=p^2 implies abelian." />,
    );
    // The card's title is what separates a draft from the evidence panels; a
    // repeated "Draft only — not checked" badge said it a second time and was
    // removed. The advisory contract itself is server-side and unchanged.
    expect(html).toContain("Instant draft");
    // neutral amber/stone — never the earned evidence green.
    expect(html).not.toContain("bg-green");
    expect(html).not.toContain("text-green");
    expect(html).not.toContain("emerald");
    expect(html).not.toContain("earned");
    // shows a working state before any line arrives.
    expect(html).toContain("Drafting an initial read");
  });

  it("marks the draft superseded when the evidence panels are authoritative", () => {
    const html = renderToStaticMarkup(
      <InstantDraft claimText="c" superseded />,
    );
    expect(html).toContain("supersede this draft");
    expect(html).toContain("opacity-60");
  });
});
