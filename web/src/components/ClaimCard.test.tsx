import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ClaimCard } from "./Workspace";

describe("ClaimCard", () => {
  it("renders a clean summary before raw hostile input", () => {
    const html = renderToStaticMarkup(
      <ClaimCard
        missing={false}
        claim={{
          text: "Claim <script>alert(1)</script> with \\href{javascript:alert(1)}{link}",
          status_sentence: "",
          human_review_required: false,
        }}
      />,
    );
    expect(html).toContain("Unsafe markup hidden");
    expect(html).toContain("Show raw input");
    expect(html.toLowerCase()).not.toContain("<script");
    expect(html.toLowerCase()).not.toContain("javascript:");
    expect(html).not.toContain("\\href");
  });

  it("keeps raw input collapsed by default", () => {
    const raw = "Raw <img src=x onerror=alert(1)> claim";
    const html = renderToStaticMarkup(
      <ClaimCard
        missing={false}
        claim={{
          text: "Clean claim",
          raw_text: raw,
          status_sentence: "",
          human_review_required: true,
        }}
      />,
    );
    expect(html).toContain("Human review required");
    expect(html).toContain("Show raw input");
    expect(html).toContain("Clean claim");
    expect(html).not.toContain("onerror");
  });

  it("does not treat a raw-only claim as missing", () => {
    const html = renderToStaticMarkup(
      <ClaimCard
        missing={false}
        claim={{
          text: "",
          raw_text: "Raw-only claim text",
          status_sentence: "",
          human_review_required: false,
        }}
      />,
    );
    expect(html).toContain("Raw-only claim text");
    expect(html).not.toContain("Not run yet");
  });
});
