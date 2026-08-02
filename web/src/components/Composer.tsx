import { useEffect, useState } from "react";
import { ArrowUp, Loader2, Target, X, Zap } from "lucide-react";
import { createRun, request } from "../lib/api";
import { useJob } from "../hooks/useJob";
import { Textarea } from "./ui/textarea";
import { JobChip } from "./JobChip";
import { MathText } from "./MathText";
import { cn } from "../lib/cn";
import type { ActiveObject } from "../lib/types";

/** The kernel's effort enum, with the words a person uses for it. */
export const EFFORTS = [
  { value: "quick_test", label: "Quick" },
  { value: "normal_pass", label: "Normal" },
  { value: "deep_exploration", label: "Deep" },
] as const;

/**
 * Bottom composer: state a claim / ask Espresso. Submitting starts a run
 * when no run is selected, or sends a conversational preview message to the
 * selected run. This is the cockpit's single command layer.
 *
 * The footer row carries only controls that DO something: the executor this
 * server is configured to call (read-only — the model's own identity is known
 * per call, not up front), and the effort the next route continuation runs at.
 * A control that changed nothing would be worse than no control.
 */
export function Composer({
  runId,
  onRunCreated,
  onSubmitClaim,
  onChatMessage,
  chatBusy,
  activeObject,
  onClearActiveObject,
  effort,
  onEffortChange,
}: {
  runId: string | null;
  onRunCreated: (runId: string) => void;
  onSubmitClaim?: (claim: string) => void;
  onChatMessage: (message: string) => Promise<void>;
  chatBusy: boolean;
  activeObject: ActiveObject;
  onClearActiveObject: () => void;
  effort: string;
  onEffortChange: (effort: string) => void;
}) {
  const [text, setText] = useState("");
  const [executor, setExecutor] = useState("");
  const job = useJob();
  const busy = job.busy || chatBusy;
  const hasRun = runId !== null;

  useEffect(() => {
    let live = true;
    request<{ executor?: string }>("/api/info")
      .then((info) => {
        // Show the last segment of the configured command: the whole module
        // path does not fit a chip and adds nothing a reader wants.
        const raw = info.executor ?? "";
        if (live) setExecutor(raw.split(".").pop() ?? raw);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const startRun = (trimmed: string) => {
    // Fire the instant draft NOW, before run creation, so it is never
    // gated on a slow workflow.
    onSubmitClaim?.(trimmed);
    job.start(
      () => createRun(trimmed),
      (result) => {
        if (result?.run_id) {
          setText("");
          onRunCreated(result.run_id);
        }
      },
    );
  };

  const submit = () => {
    const trimmed = text.trim();
    if (trimmed === "" || busy) return;
    if (hasRun) {
      setText("");
      void onChatMessage(trimmed);
      return;
    }
    startRun(trimmed);
  };

  return (
    <div className="w-full">
      {activeObject && (
        <div className="mb-2 flex max-w-full flex-wrap items-center gap-2 rounded-md border border-indigo-200 bg-indigo-50/70 px-2 py-1.5 text-[13px] text-indigo-900">
          <span className="inline-flex items-center gap-1 font-medium">
            <Target className="h-3.5 w-3.5" aria-hidden />
            Active {activeObject.kind}
          </span>
          <span className="min-w-0 flex-1 break-words">
            <MathText>{activeObject.title}</MathText>
          </span>
          <button
            type="button"
            onClick={onClearActiveObject}
            className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-indigo-700 hover:bg-indigo-100"
            aria-label="Clear active object"
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        </div>
      )}

      <div className="rounded-2xl border border-stone-300 bg-white shadow-sm focus-within:border-stone-400">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={
            hasRun
              ? activeObject
                ? `Continue about ${activeObject.kind}: ${activeObject.title}`
                : "Continue this problem — strategies, examples, literature, next move"
              : "State a mathematical claim to investigate"
          }
          rows={2}
          disabled={busy}
          className="min-h-[52px] w-full resize-none border-0 bg-transparent px-3.5 pb-1 pt-3 text-[15px] shadow-none focus-visible:ring-0"
          aria-label="Command Espresso"
        />

        <div className="flex items-center gap-2 px-2.5 pb-2 pt-0.5">
          {executor && (
            <span
              className="inline-flex items-center gap-1 text-[13px] font-medium text-stone-700"
              title="The agent command this server is configured to call"
            >
              <Zap className="h-3.5 w-3.5" aria-hidden />
              {executor}
            </span>
          )}
          <label className="inline-flex items-center gap-1 text-[13px] text-stone-500">
            <span className="sr-only">Effort for the next route continuation</span>
            <select
              value={effort}
              onChange={(e) => onEffortChange(e.target.value)}
              className="cursor-pointer rounded border-0 bg-transparent py-0.5 pl-0.5 pr-4 text-[13px] text-stone-500 hover:text-stone-700 focus:outline-none focus-visible:ring-1 focus-visible:ring-stone-300"
              title="Effort used when you continue a route"
            >
              {EFFORTS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <div className="ml-auto flex items-center gap-2">
            <JobChip phase={job.phase} error={job.error} doneLabel="run created" />
            {chatBusy && (
              <span className="inline-flex items-center gap-1 text-[13px] text-stone-500">
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                streaming
              </span>
            )}
            <button
              type="button"
              onClick={submit}
              disabled={busy || text.trim() === ""}
              aria-label="Send command to Espresso"
              className={cn(
                "inline-flex h-8 w-8 items-center justify-center rounded-full transition-colors",
                busy || text.trim() === ""
                  ? "bg-stone-200 text-stone-400"
                  : "bg-stone-800 text-white hover:bg-stone-700",
              )}
            >
              {chatBusy ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <ArrowUp className="h-4 w-4" aria-hidden />
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
