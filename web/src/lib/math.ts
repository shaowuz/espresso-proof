import katex from "katex";

/**
 * KaTeX is a DISPLAY layer only. It renders math; it never validates,
 * interprets, or upgrades any mathematical status, and it never introduces an
 * HTML-injection path:
 *
 * - text between math delimiters is returned as plain `text` segments that the
 *   React layer renders as escaped text nodes (no innerHTML);
 * - only `math` segments are handed to KaTeX, and only with `trust: false` +
 *   `strict: "ignore"`, so dangerous commands (\href, \url, \includegraphics,
 *   raw \html…) are refused — the output is KaTeX-controlled markup, never
 *   user HTML;
 * - malformed TeX (`throwOnError: false` plus a defensive try/catch) falls back
 *   to the original, copyable source text — rendering can never break reading
 *   or corrupt anything.
 */

export type Segment =
  | { type: "text"; value: string }
  | { type: "math"; value: string; display: boolean };

// Delimiters, longest-first so $$ wins over $ and \[ over \(.
const DELIMS: { open: string; close: string; display: boolean }[] = [
  { open: "$$", close: "$$", display: true },
  { open: "\\[", close: "\\]", display: true },
  { open: "\\(", close: "\\)", display: false },
  { open: "$", close: "$", display: false },
];

/**
 * Split a string into text and math segments. Unbalanced or empty delimiters
 * are left as literal text (never half-rendered). Pure and side-effect free.
 */
export function splitMath(input: string): Segment[] {
  const text = String(input ?? "");
  const segments: Segment[] = [];
  let buf = "";
  let i = 0;

  const flush = () => {
    if (buf !== "") {
      segments.push({ type: "text", value: buf });
      buf = "";
    }
  };

  const isDigit = (c: string | undefined) => c !== undefined && c >= "0" && c <= "9";
  const isSpace = (c: string | undefined) => c !== undefined && /\s/.test(c);

  outer: while (i < text.length) {
    for (const d of DELIMS) {
      if (!text.startsWith(d.open, i)) continue;
      // a single-$ open must not match an escaped \$.
      if (d.open === "$" && i > 0 && text[i - 1] === "\\") continue;
      const contentStart = i + d.open.length;
      // Single-$ inline math uses the standard markdown/KaTeX heuristic so a
      // bare currency $ is never mistaken for math: the char after the opening
      // $ must not be a space or a digit ("$5", "$ x" stay text).
      if (d.open === "$" && (isSpace(text[contentStart]) || isDigit(text[contentStart]))) continue;
      let closeAt = text.indexOf(d.close, contentStart);
      if (d.open === "$") {
        // and the closing $ must not be preceded by a space or followed by a
        // digit ("...the $10 book" does not close a math span).
        while (
          closeAt !== -1 &&
          (isSpace(text[closeAt - 1]) || isDigit(text[closeAt + 1]))
        ) {
          closeAt = text.indexOf(d.close, closeAt + 1);
        }
      }
      if (closeAt === -1) continue; // no valid closing delimiter -> literal text
      const inner = text.slice(contentStart, closeAt);
      if (inner.trim() === "") continue; // empty math -> literal text
      flush();
      segments.push({ type: "math", value: inner, display: d.display });
      i = closeAt + d.close.length;
      continue outer;
    }
    buf += text[i];
    i += 1;
  }
  flush();
  return segments;
}

/** True when the string contains at least one renderable math segment. */
export function hasMath(input: string): boolean {
  return splitMath(input).some((s) => s.type === "math");
}

/**
 * Render one TeX fragment to a sanitized KaTeX HTML string. Returns null when
 * KaTeX cannot render it safely, so the caller falls back to plain text. Never
 * throws.
 */
export function renderTeX(tex: string, display: boolean): string | null {
  try {
    return katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      // refuse \href / \url / \includegraphics / raw HTML — no injection path.
      trust: false,
      strict: "ignore",
      output: "htmlAndMathml",
    });
  } catch {
    return null;
  }
}
