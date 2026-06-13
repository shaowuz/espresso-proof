import { Loader2, CircleAlert, Check } from "lucide-react";
import type { JobPhase } from "../hooks/useJob";
import { cn } from "../lib/cn";

/**
 * Inline job feedback. Queued/running/failed are NEVER green (kernel
 * safety): activity renders gray/amber with a spinner; "done" is neutral —
 * completion of a job is not earned trust.
 */
export function JobChip({
  phase,
  error,
  doneLabel = "finished",
}: {
  phase: JobPhase;
  error: string | null;
  doneLabel?: string;
}) {
  if (phase === "idle") return null;
  if (phase === "failed") {
    return (
      <span className="inline-flex min-w-0 items-start gap-1 rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 text-[13px] leading-4 text-rose-700">
        <CircleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
        <span>{error ?? "The job failed."}</span>
      </span>
    );
  }
  const spinning = phase === "queued" || phase === "running";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[13px] font-medium leading-4",
        phase === "queued" && "border-gray-200 bg-gray-100 text-gray-600",
        phase === "running" && "border-amber-300 bg-amber-50 text-amber-800",
        phase === "done" && "border-slate-200 bg-slate-100 text-slate-700",
      )}
    >
      {spinning ? (
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
      ) : (
        <Check className="h-3 w-3" aria-hidden />
      )}
      {phase === "queued" ? "queued…" : phase === "running" ? "running…" : doneLabel}
    </span>
  );
}
