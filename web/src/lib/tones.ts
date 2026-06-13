import type { Tone } from "./types";

/**
 * THE single tone→color mapping (kernel safety).
 *
 * Nothing else in the app may produce a green *status* style: a badge may be
 * green only because the server sent tone === "earned". Brand/interactive
 * accents use the separate sage palette and are visually distinct from the
 * earned-green evidence badges.
 */
const TONE_BADGE_CLASSES: Record<Tone, string> = {
  earned: "border-green-200 bg-green-50 text-green-800",
  warn: "border-amber-300 bg-amber-50 text-amber-800",
  human: "border-violet-200 bg-violet-50 text-violet-800",
  muted: "border-gray-200 bg-gray-100 text-gray-600",
  neutral: "border-slate-200 bg-slate-100 text-slate-700",
};

const TONE_DOT_CLASSES: Record<Tone, string> = {
  earned: "bg-green-600",
  warn: "bg-amber-500",
  human: "bg-violet-500",
  muted: "bg-gray-400",
  neutral: "bg-slate-400",
};

function isTone(value: unknown): value is Tone {
  return (
    value === "earned" ||
    value === "warn" ||
    value === "human" ||
    value === "muted" ||
    value === "neutral"
  );
}

/** Defensive: unknown tones from the wire fall back to neutral, never green. */
export function toneBadgeClass(tone: unknown): string {
  return TONE_BADGE_CLASSES[isTone(tone) ? tone : "neutral"];
}

export function toneDotClass(tone: unknown): string {
  return TONE_DOT_CLASSES[isTone(tone) ? tone : "neutral"];
}
