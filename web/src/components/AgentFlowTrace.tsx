import { useState } from "react";
import { ChevronDown, ListTree, RefreshCw } from "lucide-react";
import type { TracePanel } from "../lib/types";
import { cn } from "../lib/cn";
import { Collapse, FadeIn } from "./motion";

function traceToneClass(tone: string) {
  if (tone === "warn") return "border-amber-200 bg-amber-50 text-amber-800";
  if (tone === "human") return "border-violet-200 bg-violet-50 text-violet-800";
  if (tone === "neutral") return "border-slate-200 bg-slate-50 text-slate-700";
  return "border-stone-200 bg-stone-50 text-stone-600";
}

/**
 * Collapsed-by-default AGENT_FLOW trace.
 *
 * This component renders only server-shaped display words. It never reads
 * internal artifacts/logs and never turns process state into proof/evidence styling.
 */
export function AgentFlowTrace({
  trace,
  isPending,
  isError,
  errorText,
  onRetry,
  defaultOpen = false,
}: {
  trace: TracePanel | undefined;
  isPending: boolean;
  isError: boolean;
  errorText?: string;
  onRetry?: () => void;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const items = trace && !trace.missing ? trace.items : [];
  const unavailable = trace?.unavailable === true || isError;
  const note =
    errorText ||
    trace?.advisory_note ||
    "Process trace only; mathematical trust stays in the artifact panels.";
  return (
    <section className="rounded-lg border border-stone-200 bg-white px-3 py-2.5 shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-3 text-left"
      >
        <span className="flex min-w-0 items-center gap-2">
          <ListTree className="h-4 w-4 shrink-0 text-stone-400" aria-hidden />
          <span className="min-w-0">
            <span className="block text-[14px] font-semibold uppercase tracking-wide text-stone-500">
              Agent flow trace
            </span>
            <span className="block truncate text-[13px] text-stone-500">
              Collapsed by default. Stage state is process progress only.
            </span>
          </span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5">
          {items.length > 0 && (
            <span className="rounded-full border border-stone-200 bg-stone-50 px-1.5 py-0.5 text-[12px] text-stone-500">
              {items.length} stages
            </span>
          )}
          <ChevronDown
            className={cn("h-4 w-4 text-stone-400 transition-transform", open && "rotate-180")}
            aria-hidden
          />
        </span>
      </button>

      <Collapse open={open}>
        <div className="mt-2 border-t border-stone-100 pt-2">
          {isPending && !trace ? (
            <p className="text-[13px] text-stone-500">Loading process trace...</p>
          ) : unavailable ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-2.5 py-2 text-[13px] leading-relaxed text-amber-800">
              <p>{note}</p>
              {onRetry && (
                <button
                  type="button"
                  onClick={onRetry}
                  className="mt-1 inline-flex items-center gap-1 rounded border border-amber-200 bg-white px-1.5 py-0.5 text-[12px] font-medium text-amber-800"
                >
                  <RefreshCw className="h-3 w-3" aria-hidden /> Retry
                </button>
              )}
            </div>
          ) : items.length === 0 ? (
            <p className="text-[13px] text-stone-500">No process trace is available yet.</p>
          ) : (
            <FadeIn motionKey={items.length}>
              <div className="flex flex-col gap-1.5">
                {items.map((item) => (
                  <div
                    key={item.stage_id}
                    className="rounded-md border border-stone-100 bg-stone-50/70 px-2.5 py-2"
                  >
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="min-w-0 flex-1 truncate text-[14px] font-medium text-stone-800">
                        {item.display_name || item.stage_id}
                      </span>
                      <span
                        className={cn(
                          "rounded-full border px-1.5 py-0.5 text-[12px] font-medium",
                          traceToneClass(item.tone),
                        )}
                      >
                        {item.state_word}
                      </span>
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-stone-500">
                      <span>{item.group}</span>
                      <span aria-hidden>/</span>
                      <span>{item.evidence_role_word}</span>
                      {item.updated_at !== "" && (
                        <>
                          <span aria-hidden>/</span>
                          <span>{item.updated_at}</span>
                        </>
                      )}
                    </div>
                    {item.blocked_reason !== "" && (
                      <p className="mt-1 text-[13px] text-amber-800">{item.blocked_reason}</p>
                    )}
                    {item.status_detail !== "" && item.status_detail !== item.blocked_reason && (
                      <p className="mt-1 text-[13px] text-stone-500">{item.status_detail}</p>
                    )}
                    <p className="mt-1 text-[12px] italic text-stone-400">
                      {item.advisory_note || "Process trace only; not a proof or evidence signal."}
                    </p>
                  </div>
                ))}
              </div>
            </FadeIn>
          )}
        </div>
      </Collapse>
    </section>
  );
}
