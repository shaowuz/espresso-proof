import { renderTeX, splitMath } from "./math";

export type ClaimTextWarning =
  | "unsafe_markup"
  | "html_like_markup"
  | "javascript_url"
  | "dangerous_tex_command"
  | "malformed_tex"
  | "truncated";

export type ClaimTextAnalysis = {
  summary: string;
  raw: string;
  warnings: ClaimTextWarning[];
  hasUnsafeMarkup: boolean;
  hasMalformedTex: boolean;
  isTruncated: boolean;
};

const MAX_SUMMARY_CHARS = 360;
const FALLBACK = "Claim needs clarification before it can be displayed cleanly.";

export function analyzeClaimText(rawInput: string | null | undefined): ClaimTextAnalysis {
  const raw = String(rawInput ?? "");
  const warnings = new Set<ClaimTextWarning>();

  if (/<\s*script\b/i.test(raw)) warnings.add("unsafe_markup");
  const htmlLikePattern = /<\/?\s*[a-z][a-z0-9:-]*(?:[\s/>][^<>]*)?>/i;
  if (htmlLikePattern.test(raw)) {
    warnings.add("html_like_markup");
  }
  if (/javascript\s*:/i.test(raw)) warnings.add("javascript_url");
  if (/\\(?:href|url)\b/i.test(raw)) warnings.add("dangerous_tex_command");
  if (hasMalformedTex(raw)) warnings.add("malformed_tex");

  let summary = raw
    .replace(/<\s*script\b[^>]*>[\s\S]*?<\s*\/\s*script\s*>/gi, " ")
    .replace(/<\s*script\b[^>]*>?/gi, " ")
    .replace(/<\/?\s*[a-z][a-z0-9:-]*(?:[\s/>][^<>]*)?>/gi, " ")
    .replace(/javascript\s*:[^\s)}\]]*/gi, " ")
    .replace(/\\href\s*\{[^{}]*\}\s*\{([^{}]*)\}/gi, "$1")
    .replace(/\\url\s*\{[^{}]*\}/gi, " ");

  summary = hideMalformedMath(summary);
  summary = normalizeWhitespace(summary);

  if (summary.length > MAX_SUMMARY_CHARS) {
    summary = `${summary.slice(0, MAX_SUMMARY_CHARS - 1).trimEnd()}…`;
    warnings.add("truncated");
  }

  if (summary === "") summary = FALLBACK;

  return {
    summary,
    raw,
    warnings: [...warnings],
    hasUnsafeMarkup:
      warnings.has("unsafe_markup") ||
      warnings.has("html_like_markup") ||
      warnings.has("javascript_url") ||
      warnings.has("dangerous_tex_command"),
    hasMalformedTex: warnings.has("malformed_tex"),
    isTruncated: warnings.has("truncated"),
  };
}

function normalizeWhitespace(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function hasMalformedTex(text: string): boolean {
  if (hasUnbalancedMathDelimiters(text) || hasUnbalancedBraces(text)) return true;
  return splitMath(text).some((segment) => {
    if (segment.type !== "math") return false;
    const html = renderTeX(segment.value, segment.display);
    return html === null || html.includes("katex-error");
  });
}

function hasUnbalancedMathDelimiters(text: string): boolean {
  const singleDollars = text.match(/(?<!\\)\$/g)?.length ?? 0;
  if (singleDollars % 2 !== 0) return true;
  return (
    (text.includes("\\(") && !text.includes("\\)")) ||
    (text.includes("\\[") && !text.includes("\\]"))
  );
}

function hasUnbalancedBraces(text: string): boolean {
  const texLike = /\\[a-zA-Z]+/.test(text);
  if (!texLike) return false;
  let depth = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "\\" && i + 1 < text.length) {
      i += 1;
      continue;
    }
    if (text[i] === "{") depth += 1;
    if (text[i] === "}") depth -= 1;
    if (depth < 0) return true;
  }
  return depth !== 0;
}

function hideMalformedMath(text: string): string {
  return splitMath(text)
    .map((segment) => {
      if (segment.type === "text") return segment.value;
      const html = renderTeX(segment.value, segment.display);
      if (html === null || html.includes("katex-error")) return " [malformed TeX hidden] ";
      const open = segment.display ? "$$" : "$";
      const close = segment.display ? "$$" : "$";
      return `${open}${segment.value}${close}`;
    })
    .join("");
}
