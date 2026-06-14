import { describe, expect, it } from "vitest";
import { analyzeClaimText } from "./claimText";

describe("analyzeClaimText", () => {
  it("detects script markup and omits it from the clean summary", () => {
    const result = analyzeClaimText("Show <script>alert(1)</script> for $x^2$");
    expect(result.warnings).toContain("unsafe_markup");
    expect(result.summary.toLowerCase()).not.toContain("<script");
    expect(result.summary).toContain("$x^2$");
  });

  it("detects HTML-like tags and preserves raw input exactly", () => {
    const raw = "Let <div onclick='x'>G</div> be finite.";
    const result = analyzeClaimText(raw);
    expect(result.raw).toBe(raw);
    expect(result.warnings).toContain("html_like_markup");
    expect(result.summary).not.toContain("<div");
  });

  it("detects malformed HTML-like tags", () => {
    const result = analyzeClaimText("Claim <svg/onload=alert(1)> stays text.");
    expect(result.warnings).toContain("html_like_markup");
    expect(result.summary).not.toContain("<svg");
  });

  it("detects javascript URLs", () => {
    const result = analyzeClaimText("See javascript:alert(1) before continuing.");
    expect(result.warnings).toContain("javascript_url");
    expect(result.summary.toLowerCase()).not.toContain("javascript:");
  });

  it("detects dangerous TeX URL commands and keeps only safe label text", () => {
    const result = analyzeClaimText("\\href{javascript:alert(1)}{click} then $x$");
    expect(result.warnings).toContain("dangerous_tex_command");
    expect(result.summary.toLowerCase()).not.toContain("javascript:");
    expect(result.summary).not.toContain("\\href");
    expect(result.summary).toContain("click");
  });

  it("detects url commands", () => {
    const result = analyzeClaimText("\\url{javascript:alert(1)} and continue");
    expect(result.warnings).toContain("dangerous_tex_command");
    expect(result.summary).not.toContain("\\url");
  });

  it("truncates long raw input predictably", () => {
    const result = analyzeClaimText("a".repeat(500));
    expect(result.warnings).toContain("truncated");
    expect(result.summary.length).toBeLessThanOrEqual(360);
    expect(result.summary.endsWith("…")).toBe(true);
  });

  it("hides malformed TeX without throwing", () => {
    const result = analyzeClaimText("The expression $\\frac{$ appears.");
    expect(result.warnings).toContain("malformed_tex");
    expect(result.summary).toContain("malformed TeX hidden");
    expect(result.summary).not.toContain("\\frac");
  });

  it("falls back when cleanup leaves no readable claim", () => {
    const result = analyzeClaimText("<script>alert(1)</script>");
    expect(result.summary).toBe("Claim needs clarification before it can be displayed cleanly.");
  });
});
