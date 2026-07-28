import { Fragment } from "react";
import { renderTeX, splitMath } from "../lib/math";

/**
 * Render mixed text + TeX. Display-only — never changes status.
 *
 * Text segments render as escaped React text nodes (no innerHTML). Only KaTeX
 * output (trust: false) is injected, and only for a segment KaTeX could render
 * safely; a fragment KaTeX refuses falls back to its original, copyable source
 * text. The raw source is also kept in the title attribute so the user can read
 * or copy the TeX.
 */
export function MathText({
  children,
  className,
  inline = false,
}: {
  children: string | null | undefined;
  className?: string;
  /** Force display math to render inline — for single-line/truncated surfaces
   *  (headers, item titles) where a block <div> would break the layout and
   *  produce invalid <div>-in-<p>/<span> nesting. */
  inline?: boolean;
}) {
  const text = children ?? "";
  if (text === "") return null;
  const segments = splitMath(text);

  return (
    <span className={className}>
      {segments.map((seg, i) => {
        if (seg.type === "text") {
          return <Fragment key={i}>{seg.value}</Fragment>;
        }
        const display = seg.display && !inline;
        const html = renderTeX(seg.value, display);
        if (html === null) {
          // KaTeX refused it: show the copyable source, never a broken render.
          const raw = seg.display ? `$$${seg.value}$$` : `$${seg.value}$`;
          return <Fragment key={i}>{raw}</Fragment>;
        }
        const Tag = display ? "div" : "span";
        return (
          <Tag
            key={i}
            // the ONLY innerHTML in the app: KaTeX-sanitized markup (trust:
            // false). The text around it is escaped above; no user HTML passes.
            className={display ? "math-block" : "math-inline"}
            title={seg.value}
            dangerouslySetInnerHTML={{ __html: html }}
          />
        );
      })}
    </span>
  );
}
