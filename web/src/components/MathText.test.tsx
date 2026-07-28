import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MathText } from "./MathText";

/**
 * Component-level guard: the security-critical decision lives in
 * MathText — text segments must render as ESCAPED React text nodes and only
 * KaTeX output may be injected. renderToStaticMarkup gives us the emitted HTML
 * with no DOM dependency.
 */
function html(children: string, inline = false): string {
  return renderToStaticMarkup(<MathText inline={inline}>{children}</MathText>);
}

describe("MathText", () => {
  it("escapes a raw <script> in a text segment (never innerHTML'd)", () => {
    const out = html("<script>alert(1)</script> and $x^2$");
    expect(out).not.toContain("<script>");
    expect(out).toContain("&lt;script&gt;");
    // the math part is the only injected markup, and it is KaTeX's.
    expect(out).toContain("katex");
  });

  it("renders inline math as a span and block math as a div", () => {
    expect(html("$x^2$")).toContain('class="math-inline"');
    const block = html("$$\\int_0^1 x\\,dx$$");
    expect(block).toContain('class="math-block"');
    expect(block).toContain("katex-display");
  });

  it("inline prop forces display math to render inline (no <div> in a title)", () => {
    const out = html("$$\\int_0^1 x\\,dx$$", /* inline */ true);
    expect(out).toContain('class="math-inline"');
    expect(out).not.toContain('class="math-block"');
    expect(out).not.toContain("<div");
  });

  it("never emits a script tag or event handler for a malicious field", () => {
    const out = html(
      "before $\\href{javascript:alert(1)}{x}$ </span><script>alert(2)</script> after",
    ).toLowerCase();
    expect(out).not.toContain("<script");
    expect(/\son\w+\s*=/.test(out)).toBe(false);
    expect(/href\s*=\s*["']?javascript:/.test(out)).toBe(false);
    // the surrounding prose still reads.
    expect(out).toContain("before");
    expect(out).toContain("after");
  });

  it("keeps the raw source in the title attribute for copyability", () => {
    expect(html("$G/Z(G)$")).toContain('title="G/Z(G)"');
  });
});
