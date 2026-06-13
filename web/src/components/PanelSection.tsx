import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Download, Loader2, Play, RefreshCw, Square } from "lucide-react";
import { runAgent } from "../lib/api";
import { useJob } from "../hooks/useJob";

/**
 * Minimal run/stop toggle next to a panel title: starts the panel's agent, or
 * stops it (best-effort) while running. Process control only — running/queued
 * are amber/neutral, never green; running never implies earned evidence.
 */
function RunToggle({
  runId,
  agent,
  onRan,
}: {
  runId: string;
  agent: string;
  onRan: () => void;
}) {
  const job = useJob();
  const busy = job.busy;
  return (
    <button
      type="button"
      onClick={() =>
        busy ? job.stop() : job.start(() => runAgent(runId, agent), () => onRan())
      }
      title={busy ? "Stop" : "Run this agent"}
      aria-label={busy ? `Stop ${agent}` : `Run ${agent}`}
      className={cn(
        "inline-flex h-4 w-4 items-center justify-center rounded transition-colors",
        busy
          ? "text-amber-600 hover:bg-amber-50"
          : "text-stone-300 hover:bg-stone-100 hover:text-stone-600",
      )}
    >
      {busy ? <Square className="h-2.5 w-2.5 fill-current" /> : <Play className="h-3 w-3" />}
    </button>
  );
}

/**
 * Minimal download affordance next to a panel title: exports this run's
 * validated artifact for the panel. Active only once there is data to export;
 * a read-only export — it never changes status.
 */
function DownloadArtifact({
  runId,
  panel,
  label,
  enabled,
}: {
  runId: string;
  panel: string;
  label: string;
  enabled: boolean;
}) {
  const href = `/api/runs/${encodeURIComponent(runId)}/artifact/${encodeURIComponent(panel)}`;
  const base =
    "inline-flex h-4 w-4 items-center justify-center rounded text-stone-300 transition-colors";
  if (!enabled) {
    return (
      <span
        className={cn(base, "cursor-default opacity-40")}
        title={`No ${label} to download yet`}
        aria-hidden
      >
        <Download className="h-3 w-3" />
      </span>
    );
  }
  return (
    <a
      href={href}
      download
      className={cn(base, "hover:bg-stone-100 hover:text-stone-600")}
      title={`Download ${label} (artifact)`}
      aria-label={`Download ${label} artifact`}
    >
      <Download className="h-3 w-3" />
    </a>
  );
}
import type { AgentState } from "../lib/types";
import { cn } from "../lib/cn";
import { PanelPlaceholder } from "./ExpandableCard";
import { FadeIn } from "./motion";

/** Honest per-panel process chip. NEVER green: process state is not evidence.
 * Only ACTIVE / notable states show a chip; the resting states (ready, not_run,
 * and a plain finished/done) are unlabelled — a finished panel needs no badge. */
export function AgentStateChip({ state }: { state: AgentState }) {
  const map: Record<string, { label: string; cls: string; spin?: boolean }> = {
    running: { label: "running", cls: "border-amber-300 bg-amber-50 text-amber-800", spin: true },
    queued: { label: "queued", cls: "border-stone-300 bg-stone-100 text-stone-600", spin: true },
    failed: { label: "agent failed", cls: "border-rose-200 bg-rose-50 text-rose-700" },
    skipped: { label: "skipped", cls: "border-stone-200 bg-stone-100 text-stone-500" },
  };
  const c = map[state];
  if (!c) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[12px] font-medium",
        c.cls,
      )}
    >
      {c.spin && <Loader2 className="h-2.5 w-2.5 animate-spin" aria-hidden />}
      {c.label}
    </span>
  );
}

/**
 * One independently-loading panel. Renders its own loading / error / missing
 * state so a slow or failed sibling never blanks or blocks it. A failed query
 * keeps the last good slice visible (when present) and offers a retry — a
 * cache miss never masquerades as earned evidence.
 */
export function PanelSection({
  icon: Icon,
  title,
  count,
  agentState,
  isPending,
  isError,
  errorText,
  hasData,
  emptyText,
  onRetry,
  download,
  run,
  children,
}: {
  icon?: LucideIcon;
  title: string;
  count?: number | null;
  agentState?: AgentState;
  isPending: boolean;
  isError: boolean;
  errorText?: string;
  hasData: boolean;
  emptyText: string;
  onRetry?: () => void;
  /** When set, a small download button exports this run's artifact for the
   *  panel (active only once there is data to export). */
  download?: { runId: string; panel: string; label: string };
  /** When set, a small run/stop toggle drives this panel's agent. */
  run?: { runId: string; agent: string; onRan: () => void };
  children?: ReactNode;
}) {
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5">
        {Icon && <Icon className="h-3.5 w-3.5 text-stone-400" aria-hidden />}
        <h2 className="text-[14px] font-semibold uppercase tracking-wide text-stone-500">
          {title}
        </h2>
        {count != null && <span className="text-[13px] text-stone-400">{count}</span>}
        {run && <RunToggle {...run} />}
        {download && <DownloadArtifact {...download} enabled={hasData} />}
        {agentState && <AgentStateChip state={agentState} />}
        {isPending && !hasData && (
          <Loader2 className="h-3 w-3 animate-spin text-stone-300" aria-hidden />
        )}
      </div>

      {isError ? (
        <div className="flex flex-col gap-1.5 rounded-lg border border-rose-200 bg-rose-50/70 px-2.5 py-2 text-[14px] text-rose-700">
          <span>{errorText ?? "This panel could not load."}</span>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex w-fit items-center gap-1 rounded-md border border-rose-200 bg-white px-1.5 py-0.5 text-[13px] font-medium text-rose-700 hover:bg-rose-50"
            >
              <RefreshCw className="h-3 w-3" aria-hidden /> Retry
            </button>
          )}
        </div>
      ) : isPending && !hasData ? (
        <PanelPlaceholder text="Loading…" />
      ) : !hasData ? (
        <PanelPlaceholder text={emptyText} />
      ) : (
        // fade content in when it first arrives (skeleton -> content); a poll
        // refresh with the same hasData does not re-trigger it.
        <FadeIn motionKey="loaded">{children}</FadeIn>
      )}
    </section>
  );
}
