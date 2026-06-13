import { describe, expect, it } from "vitest";
import { hasMath, renderTeX, splitMath } from "./math";

describe("splitMath", () => {
  it("returns a single text segment when there is no math", () => {
    expect(splitMath("Every group of order 6 is not abelian")).toEqual([
      { type: "text", value: "Every group of order 6 is not abelian" },
    ]);
  });

  it("splits inline $...$ math", () => {
    const segs = splitMath("the order |G| = p^2 means $G/Z(G)$ is cyclic");
    expect(segs).toEqual([
      { type: "text", value: "the order |G| = p^2 means " },
      { type: "math", value: "G/Z(G)", display: false },
      { type: "text", value: " is cyclic" },
    ]);
  });

  it("splits inline \\(...\\) math", () => {
    expect(splitMath("\\(f: \\mathbb{R} \\to \\mathbb{R}\\)")).toEqual([
      { type: "math", value: "f: \\mathbb{R} \\to \\mathbb{R}", display: false },
    ]);
  });

  it("splits block $$...$$ and \\[...\\] math as display mode", () => {
    expect(splitMath("$$\\int_0^1 x\\,dx$$")[0]).toEqual({
      type: "math",
      value: "\\int_0^1 x\\,dx",
      display: true,
    });
    expect(splitMath("\\[A \\subseteq B\\]")[0]).toMatchObject({
      type: "math",
      display: true,
    });
  });

  it("leaves an unbalanced or empty delimiter as literal text", () => {
    expect(splitMath("a price of $5 for the set")).toEqual([
      { type: "text", value: "a price of $5 for the set" },
    ]);
    expect(splitMath("$$")).toEqual([{ type: "text", value: "$$" }]);
    expect(splitMath("$  $")).toEqual([{ type: "text", value: "$  $" }]);
  });

  it("does not open on an escaped \\$", () => {
    expect(splitMath("cost \\$3 then $x$ done")).toEqual([
      { type: "text", value: "cost \\$3 then " },
      { type: "math", value: "x", display: false },
      { type: "text", value: " done" },
    ]);
  });

  it("keeps a raw <script> in a TEXT segment (never handed to a renderer)", () => {
    const segs = splitMath("<script>alert(1)</script> and $x^2$");
    expect(segs[0]).toEqual({ type: "text", value: "<script>alert(1)</script> and " });
    expect(segs[1]).toEqual({ type: "math", value: "x^2", display: false });
  });

  it("does not parse bare currency as math (two-dollar false positive)", () => {
    expect(splitMath("a $5 discount on the $10 book is fair")).toEqual([
      { type: "text", value: "a $5 discount on the $10 book is fair" },
    ]);
    // a space right after the opening $ also stays text.
    expect(splitMath("pay $ 5 now")).toEqual([{ type: "text", value: "pay $ 5 now" }]);
  });

  it("still parses real inline math adjacent to prose with a stray currency $", () => {
    // "$x^2$" is real math; a later bare "$5" must not swallow it.
    const segs = splitMath("the bound $x^2$ costs about $5");
    expect(segs).toEqual([
      { type: "text", value: "the bound " },
      { type: "math", value: "x^2", display: false },
      { type: "text", value: " costs about $5" },
    ]);
  });

  it("hasMath reflects whether any math segment exists", () => {
    expect(hasMath("plain text")).toBe(false);
    expect(hasMath("$x$")).toBe(true);
  });
});

describe("renderTeX", () => {
  it("renders valid inline TeX to KaTeX markup", () => {
    const html = renderTeX("|G| = p^2", false);
    expect(html).toBeTypeOf("string");
    expect(html).toContain("katex");
    expect(html).not.toContain("displaymode-display");
  });

  it("renders block TeX in display mode", () => {
    const html = renderTeX("\\frac{a}{b}", true);
    expect(html).toContain("katex-display");
  });

  it("renders malformed TeX without throwing (throwOnError: false)", () => {
    // KaTeX emits an error-styled span rather than throwing; the caller still
    // gets a string, and the UI keeps the copyable source alongside.
    const html = renderTeX("\\frac{", false);
    expect(html).toBeTypeOf("string");
  });

  it("refuses dangerous commands under trust: false — no executable injection", () => {
    // KaTeX renders \href as an ERROR under trust:false: the "javascript:"
    // appears only as escaped error text, never as a clickable href/src or a
    // script. Assert the real injection vectors are absent.
    const html = (renderTeX("\\href{javascript:alert(1)}{click}", false) ?? "").toLowerCase();
    expect(/href\s*=/.test(html)).toBe(false);
    expect(/src\s*=/.test(html)).toBe(false);
    expect(html).not.toContain("<script");
    expect(/on\w+\s*=/.test(html)).toBe(false); // no onerror=/onclick= handlers
    const html2 = (renderTeX("\\url{javascript:alert(1)}", false) ?? "").toLowerCase();
    expect(/href\s*=/.test(html2)).toBe(false);
  });

  it("never emits a script tag or event handler for arbitrary input", () => {
    for (const tex of [
      "x^2",
      "\\frac{1}{2}",
      "\\href{x}{y}",
      "\\foo",
      "<b>hi</b>",
      "</span><script>alert(1)</script>",
      "\\includegraphics{x}",
    ]) {
      const html = (renderTeX(tex, false) ?? "").toLowerCase();
      expect(html).not.toContain("<script");
      expect(/on\w+\s*=/.test(html)).toBe(false);
    }
  });
});
