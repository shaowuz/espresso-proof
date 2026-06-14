import { useState } from "react";
import { Loader2, Send, Target, X } from "lucide-react";
import { createRun } from "../lib/api";
import { useJob } from "../hooks/useJob";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { JobChip } from "./JobChip";
import { MathText } from "./MathText";
import type { ActiveObject } from "../lib/types";

/**
 * Bottom composer: state a claim / ask Espresso. Submitting starts a run
 * when no run is selected, or sends a conversational preview message to the
 * selected run. This is the cockpit's single command layer.
 */
export function Composer({
  runId,
  onRunCreated,
  onChatMessage,
  chatBusy,
  activeObject,
  onClearActiveObject,
}: {
  runId: string | null;
  onRunCreated: (runId: string) => void;
  onChatMessage: (message: string) => Promise<void>;
  chatBusy: boolean;
  activeObject: ActiveObject;
  onClearActiveObject: () => void;
}) {
  const [text, setText] = useState("");
  const job = useJob();
  const busy = job.busy || chatBusy;
  const hasRun = runId !== null;

  const startRun = (trimmed: string) => {
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

  const submitNewRun = () => {
    const trimmed = text.trim();
    if (trimmed === "" || busy) return;
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
      <div className="flex w-full items-end gap-2">
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
                ? `Ask about active ${activeObject.kind}: ${activeObject.title} (⌘↵ to send)`
                : "Ask about this run — strategies, examples, literature, or next move (⌘↵ to send)"
              : "Ask Espresso — state a mathematical claim to investigate (⌘↵ to send)"
          }
          rows={2}
          disabled={busy}
          className="min-h-[44px] flex-1 resize-y"
          aria-label="Command Espresso"
        />
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Button
            onClick={submit}
            disabled={busy || text.trim() === ""}
            aria-label="Send command to Espresso"
          >
            {chatBusy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <Send className="h-3.5 w-3.5" aria-hidden />
            )}
            <span>{hasRun ? "Ask" : "Send"}</span>
          </Button>
          {hasRun && (
            <Button
              variant="outline"
              onClick={submitNewRun}
              disabled={busy || text.trim() === ""}
              aria-label="Start new run from command"
            >
              New run
            </Button>
          )}
          <JobChip phase={job.phase} error={job.error} doneLabel="run created" />
          {chatBusy && (
            <span className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-2 py-0.5 text-[13px] font-medium leading-4 text-amber-800">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              streaming preview…
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
