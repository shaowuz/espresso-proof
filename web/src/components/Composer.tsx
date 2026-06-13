import { useState } from "react";
import { Send } from "lucide-react";
import { createRun } from "../lib/api";
import { useJob } from "../hooks/useJob";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";
import { JobChip } from "./JobChip";

/**
 * Bottom composer: state a claim / ask Espresso. Submitting starts a run
 * through the existing validated server action; job progress is visible
 * immediately and is never styled as earned evidence.
 */
export function Composer({ onRunCreated }: { onRunCreated: (runId: string) => void }) {
  const [text, setText] = useState("");
  const job = useJob();

  const submit = () => {
    const trimmed = text.trim();
    if (trimmed === "" || job.busy) return;
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

  return (
    <div className="shrink-0 border-t border-stone-200 bg-white/90 px-4 py-3">
      <div className="mx-auto flex w-full max-w-4xl items-end gap-2">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="Ask Espresso — state a mathematical claim to investigate (⌘↵ to send)"
          rows={2}
          disabled={job.busy}
          className="min-h-[44px] flex-1 resize-y"
          aria-label="State a mathematical claim"
        />
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Button
            onClick={submit}
            disabled={job.busy || text.trim() === ""}
            aria-label="Send to Espresso"
          >
            <Send className="h-3.5 w-3.5" aria-hidden />
            <span>Send</span>
          </Button>
          <JobChip phase={job.phase} error={job.error} doneLabel="run created" />
        </div>
      </div>
    </div>
  );
}
