import { useEffect, useRef, useState } from "react";
import { Settings2, X } from "lucide-react";
import type { LatestRound, RunListItem } from "../lib/types";
import { request } from "../lib/api";
import { MathText } from "./MathText";
import { cn } from "../lib/cn";

/**
 * Codex-style bottom-left control. The main workspace stays minimal; the
 * verbose run details (latest-round ledger) and run controls (which run,
 * provider) live here, hidden until opened. Display-only — nothing here
 * changes any mathematical status.
 */
export function SettingsButton({
  runs,
  selectedRunId,
  onSelectRun,
  latestRound,
}: {
  runs: RunListItem[];
  selectedRunId: string | null;
  onSelectRun: (id: string) => void;
  latestRound: LatestRound | null | undefined;
}) {
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<{ provider?: string; runs?: number }>({});
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    request<{ provider?: string; runs?: number }>("/api/info")
      .then(setInfo)
      .catch(() => setInfo({}));
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Run details and settings"
        aria-expanded={open}
        className={cn(
          "inline-flex h-8 w-8 items-center justify-center rounded-md border text-stone-500 transition-colors",
          open ? "border-stone-300 bg-stone-100" : "border-stone-200 bg-white hover:bg-stone-50",
        )}
      >
        <Settings2 className="h-4 w-4" aria-hidden />
      </button>

      {open && (
        <div className="absolute bottom-10 left-0 z-20 w-80 rounded-lg border border-stone-200 bg-white p-3 shadow-lg">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-stone-500">
              Run details & settings
            </span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="rounded p-0.5 text-stone-400 hover:bg-stone-100"
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </button>
          </div>

          {/* Run control */}
          <Group title="Run">
            {runs.length > 0 ? (
              <select
                aria-label="Select run"
                className="w-full rounded-md border border-stone-200 bg-white px-2 py-1 text-[13px] text-stone-700"
                value={selectedRunId ?? ""}
                onChange={(e) => onSelectRun(e.target.value)}
              >
                {runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {(r.statement ?? r.claim ?? r.run_id).slice(0, 64)}
                  </option>
                ))}
              </select>
            ) : (
              <p className="text-[13px] text-stone-400">No runs yet.</p>
            )}
            <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[12px] text-stone-400">
              {info.provider && <span>provider: {info.provider}</span>}
              {info.runs != null && <span>{info.runs} run(s)</span>}
            </div>
            <p className="mt-1 text-[12px] text-stone-400">New run: use the composer at the bottom.</p>
          </Group>

          {/* Latest round (run details) */}
          <Group title="Latest round">
            {latestRound ? (
              <table className="w-full border-separate border-spacing-y-0.5 text-[13px]">
                <tbody>
                  <Row label="Selected strategy" value={latestRound.selected_strategy || "—"} />
                  <Row
                    label="Target subgoal"
                    value={
                      [latestRound.target_subgoal_id, latestRound.local_claim]
                        .filter(Boolean)
                        .join(" — ") || "—"
                    }
                  />
                  <Row label="Effort" value={latestRound.effort || "—"} />
                  <Row label="Stop reason" value={latestRound.stop_reason || "—"} />
                  <Row
                    label="Rounds used / max"
                    value={`${latestRound.rounds_used ?? "—"} / ${latestRound.max_rounds ?? "—"}`}
                  />
                  <Row
                    label="Subgoal graph changed"
                    value={latestRound.graph_changed ? "yes" : "no"}
                  />
                  <Row
                    label="Open subgoals"
                    value={latestRound.open_subgoals != null ? String(latestRound.open_subgoals) : "—"}
                  />
                  <Row
                    label="Reviewer blockers"
                    value={
                      latestRound.reviewer_blockers.length > 0
                        ? String(latestRound.reviewer_blockers.length)
                        : "none"
                    }
                  />
                </tbody>
              </table>
            ) : (
              <p className="text-[13px] text-stone-400">
                No round has run yet. Continue a route to see what the latest round did.
              </p>
            )}
            <p className="mt-1 text-[11px] italic text-stone-400">explanatory — not evidence</p>
          </Group>
        </div>
      )}
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-3 last:mb-0">
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-stone-400">
        {title}
      </div>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <tr className="align-top">
      <td className="w-[44%] py-0.5 pr-2 font-medium text-stone-500">{label}</td>
      <td className="break-words py-0.5 text-stone-700">
        <MathText>{value}</MathText>
      </td>
    </tr>
  );
}
