import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Header } from "./Header";

describe("Header", () => {
  it("renders a clean claim summary instead of raw hostile input", () => {
    const html = renderToStaticMarkup(
      <Header
        claim={{
          text: "Claim <script>alert(1)</script> \\href{javascript:alert(1)}{link}",
          status_sentence: "",
          human_review_required: false,
        }}
        status={undefined}
      />,
    );
    expect(html).toContain("Claim");
    expect(html.toLowerCase()).not.toContain("<script");
    expect(html.toLowerCase()).not.toContain("javascript:");
    expect(html).not.toContain("\\href");
  });
});
