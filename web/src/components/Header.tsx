import { FlaskConical, Loader2, Square } from "lucide-react";
import { MathText } from "./MathText";
import type { ClaimView, StatusView } from "../lib/types";
import { analyzeClaimText } from "../lib/claimText";
import { cancelJob } from "../lib/api";

/**
 * App header: brand, claim summary, run state, evidence readout. The run picker
 * lives in the bottom-left settings control (Codex-style minimal header).
 * All sentences are server copy; the evidence counts come from the status
 * query (computed server-side from validated artifacts).
 */
export function Header({
  claim,
  status,
}: {
  claim: ClaimView | undefined;
  status: StatusView | undefined;
}) {
  const claimText = claim ? analyzeClaimText(claim.text).summary : "";
  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-stone-200 bg-white/80 px-4 py-2">
      <div className="flex items-center gap-1.5 text-stone-800">
        <FlaskConical className="h-4 w-4 text-sage-600" aria-hidden />
        <span className="text-[15px] font-semibold tracking-tight">Espresso Proof</span>
      </div>

      <div className="min-w-0 flex-1">
        {claimText !== "" ? (
          <p className="truncate text-[15px] text-stone-700" title={claimText}>
            <MathText inline>{claimText}</MathText>
          </p>
        ) : (
          <p className="text-[15px] text-stone-400">No claim selected yet</p>
        )}
      </div>

      {status && (
        <div className="hidden shrink-0 items-center gap-2 text-[13px] text-stone-500 md:flex">
          <span className="rounded-full border border-stone-200 bg-stone-50 px-2 py-0.5">
            {status.state}
          </span>
        </div>
      )}

      {/* Working + Stop. A person should be able to see the run moving and end
          it whenever they want, rather than only being able to start things.
          Stopping is best-effort by contract: an in-flight model call is not
          cooperatively interruptible, so the kernel still validates whatever
          that call finishes and nothing is left in an unsafe state. */}
      {status?.active_job && (
        <div className="flex shrink-0 items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-[13px] text-stone-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            {status.active_job.action === "auto_continue"
              ? "continuing"
              : status.active_job.action}
          </span>
          <button
            type="button"
            onClick={() => {
              const jobId = status.active_job?.job_id;
              if (jobId) void cancelJob(jobId).catch(() => undefined);
            }}
            className="rounded-md border border-stone-300 bg-white px-2.5 py-1 text-[13px] font-medium text-stone-700 hover:bg-stone-50"
            aria-label="Stop the running job"
          >
            <span className="inline-flex items-center gap-1">
              <Square className="h-3 w-3" aria-hidden />
              Stop
            </span>
          </button>
        </div>
      )}

      {/* One page = one problem: a new problem opens in a NEW page with its own
          folder of artifacts; this page (and its conversation) stays. */}
      <button
        type="button"
        onClick={() => window.open(window.location.pathname, "_blank")}
        className="shrink-0 rounded-md border border-stone-200 bg-white px-2.5 py-1 text-[13px] font-medium text-stone-600 hover:bg-stone-50"
        aria-label="Open a new page for a new problem"
      >
        New problem
      </button>
    </header>
  );
}
