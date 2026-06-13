import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Loader2, RefreshCw } from "lucide-react";
import type { AgentState } from "../lib/types";
import { cn } from "../lib/cn";
import { PanelPlaceholder } from "./ExpandableCard";

/** Honest per-panel process chip. NEVER green: process state is not evidence. */
export function AgentStateChip({ state }: { state: AgentState }) {
  if (state === "ready" || state === "not_run") return null;
  const map: Record<string, { label: string; cls: string; spin?: boolean }> = {
    running: { label: "running", cls: "border-amber-300 bg-amber-50 text-amber-800", spin: true },
    queued: { label: "queued", cls: "border-stone-300 bg-stone-100 text-stone-600", spin: true },
    failed: { label: "agent failed", cls: "border-rose-200 bg-rose-50 text-rose-700" },
    skipped: { label: "skipped", cls: "border-stone-200 bg-stone-100 text-stone-500" },
    done: { label: "finished", cls: "border-stone-200 bg-stone-100 text-stone-600" },
  };
  const c = map[state];
  if (!c) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
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
  children?: ReactNode;
}) {
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-1.5">
        {Icon && <Icon className="h-3.5 w-3.5 text-stone-400" aria-hidden />}
        <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-500">
          {title}
        </h2>
        {count != null && <span className="text-[11px] text-stone-400">{count}</span>}
        {agentState && <AgentStateChip state={agentState} />}
        {isPending && !hasData && (
          <Loader2 className="h-3 w-3 animate-spin text-stone-300" aria-hidden />
        )}
      </div>

      {isError ? (
        <div className="flex flex-col gap-1.5 rounded-lg border border-rose-200 bg-rose-50/70 px-2.5 py-2 text-xs text-rose-700">
          <span>{errorText ?? "This panel could not load."}</span>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex w-fit items-center gap-1 rounded-md border border-rose-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-rose-700 hover:bg-rose-50"
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
        children
      )}
    </section>
  );
}
