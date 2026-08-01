import { FlaskConical } from "lucide-react";
import { MathText } from "./MathText";
import type { ClaimView, StatusView } from "../lib/types";
import { analyzeClaimText } from "../lib/claimText";

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
