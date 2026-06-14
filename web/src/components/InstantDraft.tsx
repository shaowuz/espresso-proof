import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { FadeIn } from "./motion";
import { MathText } from "./MathText";
import { streamInstantDraftTransient, type InstantStatus } from "../lib/instantDraft";
import { cn } from "../lib/cn";

const LABEL = "Draft only — not checked";

type Phase = "working" | "streaming" | "done";

/**
 * Goal 55: the instant conversation lane panel. It fires the moment a claim is
 * submitted — DECOUPLED from run creation — so a slow workflow can never delay
 * or blank the draft. It shows a fast, labeled DRAFT while the evidence pipeline
 * runs in the background. The draft is conversation, NOT evidence:
 *
 * - it is visibly labeled "Draft only — not checked" and styled neutral
 *   (amber/stone) — NEVER the earned evidence-green;
 * - every line was proof-language-scrubbed server-side before it arrived;
 * - it updates no evidence panel and upgrades no status;
 * - once the run has earned evidence the panels are authoritative, so the draft
 *   is dimmed and marked superseded.
 *
 * `streamer` is injectable for tests; production uses the pre-run SSE client.
 */
export function InstantDraft({
  claimText,
  superseded = false,
  streamer = streamInstantDraftTransient,
}: {
  claimText: string;
  superseded?: boolean;
  streamer?: typeof streamInstantDraftTransient;
}) {
  const [lines, setLines] = useState<string[]>([]);
  const [phase, setPhase] = useState<Phase>("working");
  const [status, setStatus] = useState<InstantStatus>("draft");
  const activeClaim = useRef<string | null>(null);

  useEffect(() => {
    if (!claimText) return;
    activeClaim.current = claimText;
    setLines([]);
    setPhase("working");
    setStatus("draft");
    const mine = claimText;
    void streamer(claimText, {
      onAck: () => {
        if (activeClaim.current === mine) setPhase("working");
      },
      onLine: (text) => {
        if (activeClaim.current !== mine) return;
        setPhase("streaming");
        setLines((prev) => [...prev, text]);
      },
      onDone: (_ok, finalStatus) => {
        if (activeClaim.current !== mine) return;
        setStatus(finalStatus);
        setPhase("done");
      },
    });
    return () => {
      // a new claim invalidates this stream's late callbacks.
      if (activeClaim.current === mine) activeClaim.current = null;
    };
  }, [claimText, streamer]);

  if (!claimText) return null;

  const unavailable = phase === "done" && lines.length === 0;

  return (
    <Card
      className={cn(
        "border-amber-200/70 bg-amber-50/40",
        superseded && "opacity-60",
      )}
    >
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="inline-flex items-center gap-1.5 text-[15px] text-stone-700">
          <Sparkles className="h-4 w-4 text-amber-500" aria-hidden />
          Instant draft
        </CardTitle>
        <span className="rounded-full border border-amber-300 bg-amber-100/70 px-2 py-0.5 text-[12px] font-medium text-amber-800">
          {LABEL}
        </span>
      </CardHeader>
      <CardContent className="pt-0 text-[14px] leading-relaxed text-stone-700">
        {superseded && (
          <p className="mb-2 rounded-md border border-amber-200 bg-amber-100/60 px-2.5 py-1.5 text-[13px] text-amber-800">
            The evidence panels are authoritative and supersede this draft.
          </p>
        )}
        {phase === "working" && lines.length === 0 && !unavailable && (
          <p className="animate-pulse text-stone-400">Drafting an initial read…</p>
        )}
        {unavailable && (
          <p className="text-stone-500">
            A draft is not available right now — the evidence pipeline above is
            still the source of truth.
          </p>
        )}
        {lines.length > 0 && (
          <div className="space-y-1.5">
            {lines.map((line, i) => (
              <FadeIn key={i}>
                <MathText
                  className={cn(
                    i === 0 && "font-medium text-amber-800",
                    "block",
                  )}
                >
                  {line}
                </MathText>
              </FadeIn>
            ))}
          </div>
        )}
        {phase === "done" && !unavailable && status !== "draft" && (
          <p className="mt-2 text-[12px] text-stone-400">
            Drafted from a fast fallback; the evidence pipeline continues.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
