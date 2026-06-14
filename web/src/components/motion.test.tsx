import { renderToStaticMarkup } from "react-dom/server";
import { MotionConfig } from "motion/react";
import { describe, expect, it } from "vitest";
import { AnimatedList, Collapse, FadeIn, PulseDot } from "./motion";

/**
 * Goal 43E safety: motion is polish only. It must never gate information —
 * open content always renders, and under reduced motion the content is still
 * present (just without the transition).
 */
function html(node: React.ReactNode, reduced = false): string {
  return renderToStaticMarkup(
    <MotionConfig reducedMotion={reduced ? "always" : "never"}>{node}</MotionConfig>,
  );
}

describe("Collapse", () => {
  it("renders its children when open", () => {
    expect(html(<Collapse open>{<span>detail</span>}</Collapse>)).toContain("detail");
  });

  it("renders nothing when closed", () => {
    expect(html(<Collapse open={false}>{<span>detail</span>}</Collapse>)).not.toContain("detail");
  });

  it("still shows content under reduced motion (motion never hides info)", () => {
    expect(html(<Collapse open>{<span>detail</span>}</Collapse>, true)).toContain("detail");
  });
});

describe("FadeIn", () => {
  it("renders children with and without reduced motion", () => {
    expect(html(<FadeIn>{<span>arrived</span>}</FadeIn>)).toContain("arrived");
    expect(html(<FadeIn>{<span>arrived</span>}</FadeIn>, true)).toContain("arrived");
  });
});

describe("AnimatedList", () => {
  it("renders all items with and without reduced motion", () => {
    const list = (
      <AnimatedList
        items={[
          { id: "a", label: "Alpha" },
          { id: "b", label: "Beta" },
        ]}
        getKey={(item) => item.id}
      >
        {(item) => <span>{item.label}</span>}
      </AnimatedList>
    );
    expect(html(list)).toContain("Alpha");
    expect(html(list)).toContain("Beta");
    expect(html(list, true)).toContain("Alpha");
    expect(html(list, true)).toContain("Beta");
  });
});

describe("PulseDot", () => {
  it("renders a span in both motion modes (no green earned styling)", () => {
    expect(html(<PulseDot className="bg-amber-500" />)).toContain("bg-amber-500");
    expect(html(<PulseDot className="bg-amber-500" />, true)).toContain("bg-amber-500");
  });
});
