import { FlaskConical } from "lucide-react";
import type { RunListItem, Snapshot } from "../lib/types";

/**
 * App header: brand, claim summary, run state, evidence readout, run picker.
 * All sentences are server copy; the evidence counts come from the snapshot's
 * status block (computed server-side from validated artifacts).
 */
export function Header({
  snapshot,
  runs,
  selectedRunId,
  onSelectRun,
}: {
  snapshot: Snapshot | null;
  runs: RunListItem[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
}) {
  const claimText = snapshot?.claim.text ?? "";
  const evidence = snapshot?.status.evidence;
  return (
    <header className="flex shrink-0 items-center gap-3 border-b border-stone-200 bg-white/80 px-4 py-2">
      <div className="flex items-center gap-1.5 text-stone-800">
        <FlaskConical className="h-4 w-4 text-sage-600" aria-hidden />
        <span className="text-sm font-semibold tracking-tight">Espresso Proof</span>
      </div>

      <div className="min-w-0 flex-1">
        {claimText !== "" ? (
          <p className="truncate text-[13px] text-stone-700" title={claimText}>
            {claimText}
          </p>
        ) : (
          <p className="text-[13px] text-stone-400">No claim selected yet</p>
        )}
        {snapshot && (
          <p className="truncate text-[11px] text-stone-500">
            {snapshot.claim.status_sentence}
          </p>
        )}
      </div>

      {snapshot && (
        <div className="hidden shrink-0 items-center gap-2 text-[11px] text-stone-500 md:flex">
          <span className="rounded-full border border-stone-200 bg-stone-50 px-2 py-0.5">
            {snapshot.status.state}
          </span>
          {evidence && (
            <span>
              evidence: {evidence.reference_supported_steps} reference-supported ·{" "}
              {evidence.machine_checks} machine checks
            </span>
          )}
        </div>
      )}

      {runs.length > 0 && (
        <select
          aria-label="Select run"
          className="max-w-[180px] shrink-0 rounded-md border border-stone-200 bg-white px-2 py-1 text-xs text-stone-700"
          value={selectedRunId ?? ""}
          onChange={(e) => onSelectRun(e.target.value)}
        >
          {runs.map((run) => (
            <option key={run.run_id} value={run.run_id}>
              {(run.statement ?? run.claim ?? run.run_id).slice(0, 60)}
            </option>
          ))}
        </select>
      )}
    </header>
  );
}
