import { useState } from "react";
import { CheckCircle2, ChevronDown, ChevronRight, Loader2, Play, Route, UserRound } from "lucide-react";
import type { AgentView, ClaimView, StatusView, StrategyView } from "../lib/types";
import { continueRoute, errorText, runAgent } from "../lib/api";
import { useClaimPanel, useStrategiesPanel } from "../hooks/useCockpit";
import { useJob } from "../hooks/useJob";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Separator } from "./ui/separator";
import { ToneBadge } from "./ToneBadge";
import { JobChip } from "./JobChip";
import { PanelPlaceholder } from "./ExpandableCard";
import { PanelSection } from "./PanelSection";
import { cn } from "../lib/cn";

function ClaimCard({ claim, missing }: { claim: ClaimView | undefined; missing: boolean }) {
  if (missing || !claim || claim.text === "") {
    return (
      <Card>
        <CardContent className="pt-2.5">
          <PanelPlaceholder text="Not run yet — the claim has not been parsed." />
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-[15px] leading-relaxed">{claim.text}</CardTitle>
          {claim.human_review_required && (
            <Badge className="shrink-0 border-violet-200 bg-violet-50 text-violet-800">
              <UserRound className="h-3 w-3" aria-hidden />
              Human review required
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-xs leading-relaxed text-stone-600">{claim.status_sentence}</p>
      </CardContent>
    </Card>
  );
}

function StrategyCard({
  strategy,
  runId,
  onChanged,
}: {
  strategy: StrategyView;
  runId: string;
  onChanged: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const job = useJob();
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return (
    <Card className={cn(strategy.selected && "border-sage-300 ring-1 ring-sage-200")}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-start gap-1.5 px-3 py-2.5 text-left hover:bg-stone-50"
      >
        <Chevron className="mt-1 h-3.5 w-3.5 shrink-0 text-stone-400" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-semibold text-stone-800">{strategy.title}</span>
            {strategy.selected && (
              <Badge className="border-sage-200 bg-sage-50 text-sage-800">
                <Route className="h-3 w-3" aria-hidden />
                selected route
              </Badge>
            )}
            {strategy.score !== null && (
              <Badge title="Advisory score — display only">
                score {strategy.score}
              </Badge>
            )}
            <ToneBadge tone={strategy.source_tone} phrase={strategy.source_phrase} />
          </span>
          {strategy.one_line !== "" && (
            <span className="mt-0.5 block text-xs leading-relaxed text-stone-600">
              {strategy.one_line}
            </span>
          )}
        </span>
      </button>
      {expanded && (
        <div className="border-t border-stone-100 px-3 py-2 pl-8 text-xs leading-relaxed text-stone-700">
          {strategy.status_sentence !== "" && (
            <p className="mb-1.5">{strategy.status_sentence}</p>
          )}
          {strategy.main_risk !== "" && (
            <p className="mb-1.5">
              <span className="font-semibold text-stone-500">Main risk: </span>
              {strategy.main_risk}
            </p>
          )}
          {strategy.next_step !== "" && (
            <p>
              <span className="font-semibold text-stone-500">Next step: </span>
              {strategy.next_step}
            </p>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-stone-100 px-3 py-1.5 pl-8">
        <Button
          size="sm"
          variant="outline"
          disabled={job.busy}
          onClick={() =>
            job.start(() => continueRoute(runId, strategy.id), () => onChanged())
          }
        >
          <Play className="h-3 w-3" aria-hidden />
          Continue this route
        </Button>
        <JobChip phase={job.phase} error={job.error} doneLabel="pass finished" />
      </div>
    </Card>
  );
}

/** Agent activity styling: never green. Active → amber + spinner. */
function agentActivity(status: string): "active" | "failed" | "idle" {
  const s = status.toLowerCase();
  if (s.includes("run") || s.includes("queue") || s.includes("progress") || s.includes("work")) {
    return "active";
  }
  if (s.includes("fail") || s.includes("error")) return "failed";
  return "idle";
}

function AgentRow({
  agent,
  runId,
  onChanged,
}: {
  agent: AgentView;
  runId: string;
  onChanged: () => void;
}) {
  const job = useJob();
  const activity = agentActivity(agent.status);
  return (
    <div className="flex flex-wrap items-center gap-2 px-3 py-2">
      <span className="min-w-0 flex-1 truncate text-xs font-medium text-stone-700">
        {agent.title}
      </span>
      <Badge
        className={cn(
          activity === "active" && "border-amber-300 bg-amber-50 text-amber-800",
          activity === "failed" && "border-rose-200 bg-rose-50 text-rose-700",
        )}
      >
        {activity === "active" && (
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        )}
        {agent.status_word !== "" ? agent.status_word : "not run yet"}
      </Badge>
      {agent.duration_seconds !== null && (
        <span className="text-[11px] tabular-nums text-stone-400">
          {Math.round(agent.duration_seconds)}s
        </span>
      )}
      <Button
        size="sm"
        variant="outline"
        disabled={job.busy}
        onClick={() => job.start(() => runAgent(runId, agent.agent), () => onChanged())}
      >
        <Play className="h-3 w-3" aria-hidden />
        Run
      </Button>
      <JobChip phase={job.phase} error={job.error} doneLabel="finished" />
    </div>
  );
}

/**
 * CENTER workspace: claim card, strategy board, agent board (Goal 43B).
 * The claim and strategies each read their own fast-core panel query, so the
 * workspace opens before evidence agents finish; the agent board reads the
 * shared status query. ``onChanged`` (passed from App) refreshes the affected
 * queries after a Continue/Run pass without a full page reload.
 */
export function Workspace({
  runId,
  status,
  onChanged,
}: {
  runId: string;
  status: StatusView | undefined;
  onChanged: () => void;
}) {
  const claim = useClaimPanel(runId);
  const strat = useStrategiesPanel(runId);
  const agents = status?.agents ?? [];
  const strategies = strat.data && !strat.data.missing ? strat.data.strategies : [];

  return (
    <div className="flex flex-col gap-4 p-4">
      <ClaimCard claim={claim.data?.claim} missing={claim.data?.missing ?? true} />

      <PanelSection
        icon={Route}
        title="Strategies"
        count={strat.data && !strat.data.missing ? strategies.length : null}
        agentState={strat.data?.agent_state}
        isPending={strat.isPending}
        isError={strat.isError}
        errorText={errorText(strat.error)}
        hasData={strategies.length > 0}
        emptyText="Not run yet — no proof routes have been explored."
        onRetry={() => void strat.refetch()}
      >
        <div className="flex flex-col gap-2">
          {status?.routes_preliminary && (
            <Badge className="w-fit border-gray-200 bg-gray-100 text-gray-600">
              preliminary
            </Badge>
          )}
          {strategies.map((s) => (
            <StrategyCard key={s.id} strategy={s} runId={runId} onChanged={onChanged} />
          ))}
        </div>
      </PanelSection>

      <section>
        <h2 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-stone-500">
          Agents
        </h2>
        {agents.length === 0 ? (
          <PanelPlaceholder text="Not run yet — no agent activity recorded for this run." />
        ) : (
          <Card>
            {agents.map((a, i) => (
              <div key={a.agent}>
                {i > 0 && <Separator />}
                <AgentRow agent={a} runId={runId} onChanged={onChanged} />
              </div>
            ))}
          </Card>
        )}
      </section>

      {status?.completed && (
        <p className="flex items-center gap-1.5 text-xs text-stone-500">
          <CheckCircle2 className="h-3.5 w-3.5 text-stone-400" aria-hidden />
          This run has finished its budgeted passes. Conclusions above carry their own
          evidence labels.
        </p>
      )}
    </div>
  );
}
