import { useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Loader2,
  Minus,
  Play,
  Route,
  Target,
  UserRound,
  Wrench,
} from "lucide-react";
import type {
  AgentView,
  ClaimView,
  FeedbackPriority,
  LedgerContext,
  LatestRound,
  StatusView,
  StrategyView,
} from "../lib/types";
import { continueRoute, errorText, postStrategyFeedback, runAgent } from "../lib/api";
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
import { MathText } from "./MathText";
import { Collapse, FadeIn } from "./motion";
import { ChatPanel } from "./ChatPanel";
import { formatDuration, timeAgo, useElapsed } from "../lib/time";

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
          <CardTitle className="text-[15px] leading-relaxed"><MathText>{claim.text}</MathText></CardTitle>
          {claim.human_review_required && (
            <Badge className="shrink-0 border-violet-200 bg-violet-50 text-violet-800">
              <UserRound className="h-3 w-3" aria-hidden />
              Human review required
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <p className="text-xs leading-relaxed text-stone-600"><MathText>{claim.status_sentence}</MathText></p>
      </CardContent>
    </Card>
  );
}

// ─── Priority button config ──────────────────────────────────────────────────

const PRIORITY_OPTIONS: {
  value: FeedbackPriority;
  label: string;
  icon: React.ElementType;
  activeClass: string;
}[] = [
  {
    value: "focus",
    label: "Focus",
    icon: Target,
    // indigo, not sage: a priority pill must read as a preference, never as
    // earned evidence-green.
    activeClass: "border-indigo-300 bg-indigo-50 text-indigo-800",
  },
  {
    value: "normal",
    label: "Normal",
    icon: Minus,
    activeClass: "border-slate-400 bg-slate-100 text-slate-800",
  },
  {
    value: "reduce",
    label: "Reduce",
    icon: ChevronDown,
    activeClass: "border-amber-400 bg-amber-50 text-amber-900",
  },
  {
    value: "repair",
    label: "Repair",
    icon: Wrench,
    activeClass: "border-violet-300 bg-violet-50 text-violet-900",
  },
];

// ─── Evidence + Time-allocation ledgers ─────────────────────────────────────

function EvidenceLedger({
  ledger,
  strategy,
}: {
  ledger: LedgerContext;
  strategy: StrategyView;
}) {
  return (
    <div className="mt-2 rounded-md border border-stone-100 bg-stone-50 p-2 text-[11px] leading-relaxed">
      <p className="mb-1 font-semibold text-stone-500 uppercase tracking-wide text-[10px]">
        Evidence ledger
      </p>
      <table className="w-full border-collapse">
        <tbody>
          <LedgerRow
            label="Source gaps"
            value={ledger.source_gaps === 0 ? "none recorded" : String(ledger.source_gaps)}
            warn={ledger.source_gaps > 0}
          />
          <LedgerRow
            label="Example pressure"
            value={ledger.example_pressure === 0 ? "none recorded" : String(ledger.example_pressure)}
          />
          <LedgerRow
            label="Counterexample pressure"
            value={
              ledger.counterexample_pressure === 0
                ? "none recorded"
                : String(ledger.counterexample_pressure)
            }
            warn={ledger.counterexample_pressure > 0}
          />
          <LedgerRow
            label="Reviewer objections"
            value={
              ledger.reviewer_objections.length === 0
                ? "none recorded"
                : ledger.reviewer_objections.join("; ")
            }
            warn={ledger.reviewer_objections.length > 0}
          />
          <LedgerRow label="Main risk" value={strategy.main_risk || "—"} warn />
          <LedgerRow
            label="What could falsify / block"
            value={ledger.next_cheapest_test || "—"}
          />
        </tbody>
      </table>
      <p className="mt-1 text-[10px] text-stone-400 italic">explanatory, not evidence</p>
    </div>
  );
}

function TimeAllocationLedger({
  ledger,
  strategy,
}: {
  ledger: LedgerContext;
  strategy: StrategyView;
}) {
  const fb = strategy.my_feedback;
  const ratingText = fb
    ? `score ${fb.score}, ${fb.priority}${fb.note ? ` — ${fb.note}` : ""}`
    : "not rated";

  return (
    <div className="mt-2 rounded-md border border-stone-100 bg-stone-50 p-2 text-[11px] leading-relaxed">
      <p className="mb-1 font-semibold text-stone-500 uppercase tracking-wide text-[10px]">
        Time-allocation ledger
      </p>
      <table className="w-full border-collapse">
        <tbody>
          <LedgerRow label="Your score / priority" value={ratingText} />
          <LedgerRow label="Selected route" value={strategy.selected ? "yes" : "no"} />
          <LedgerRow label="Effort" value={ledger.effort || "—"} />
          <LedgerRow
            label="Reviewer blockers"
            value={
              ledger.reviewer_objections.length === 0
                ? "none"
                : String(ledger.reviewer_objections.length)
            }
          />
          <LedgerRow
            label="Target subgoal"
            value={
              [ledger.target_subgoal_id, ledger.target_local_claim]
                .filter(Boolean)
                .join(" — ") || "—"
            }
          />
        </tbody>
      </table>
      <p className="mt-1 text-[10px] text-stone-400 italic">explanatory, not evidence</p>
    </div>
  );
}

function LedgerRow({
  label,
  value,
  warn = false,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <tr className="align-top">
      <td className="py-0.5 pr-2 font-medium text-stone-500 whitespace-nowrap w-[40%]">{label}</td>
      <td className={cn("py-0.5 break-words", warn ? "text-amber-800" : "text-stone-600")}>
        <MathText>{value}</MathText>
      </td>
    </tr>
  );
}

// ─── RoundLedger ─────────────────────────────────────────────────────────────

export function RoundLedger({ round }: { round: LatestRound | null | undefined }) {
  if (!round) return null;
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-[11px] leading-relaxed">
      <p className="mb-1.5 font-semibold text-slate-600 text-xs">Latest round</p>
      <table className="w-full border-collapse">
        <tbody>
          <LedgerRow label="Selected strategy" value={round.selected_strategy || "—"} />
          <LedgerRow
            label="Target subgoal"
            value={
              [round.target_subgoal_id, round.local_claim].filter(Boolean).join(" — ") || "—"
            }
          />
          <LedgerRow label="Effort" value={round.effort || "—"} />
          <LedgerRow label="Stop reason" value={round.stop_reason || "—"} />
          <LedgerRow
            label="Rounds used / max"
            value={
              round.rounds_used !== null && round.max_rounds !== null
                ? `${round.rounds_used} / ${round.max_rounds}`
                : round.rounds_used !== null
                  ? String(round.rounds_used)
                  : "—"
            }
          />
          <LedgerRow
            label="Subgoal graph changed"
            value={round.graph_changed ? "yes" : "no"}
          />
          <LedgerRow
            label="Reviewer blockers"
            value={
              round.reviewer_blockers.length === 0
                ? "none"
                : round.reviewer_blockers.join("; ")
            }
            warn={round.reviewer_blockers.length > 0}
          />
          <LedgerRow
            label="Open subgoals"
            value={round.open_subgoals !== null ? String(round.open_subgoals) : "—"}
          />
        </tbody>
      </table>
      <p className="mt-1.5 text-[10px] text-slate-400 italic">explanatory — not evidence</p>
    </div>
  );
}

// ─── Rating block inside StrategyCard ────────────────────────────────────────

function RatingBlock({
  strategy,
  runId,
  onChanged,
}: {
  strategy: StrategyView;
  runId: string;
  onChanged: () => void;
}) {
  const saved = strategy.my_feedback;
  const [priority, setPriority] = useState<FeedbackPriority>(
    isFeedbackPriority(saved?.priority) ? saved.priority : "normal",
  );
  const [score, setScore] = useState<number>(saved?.score ?? 50);
  const [note, setNote] = useState<string>(saved?.note ?? "");
  const [saving, setSaving] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [serverError, setServerError] = useState<string | null>(null);

  // the controls differ from the last saved feedback → a save would change it.
  const dirty =
    score !== (saved?.score ?? 50) ||
    priority !== (isFeedbackPriority(saved?.priority) ? saved!.priority : "normal") ||
    note.trim() !== (saved?.note ?? "");

  // clear the "saved" → "idle" timer on unmount (no setState after unmount).
  const savedTimer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(savedTimer.current), []);

  async function handleSave() {
    setSaving("saving");
    setServerError(null);
    try {
      const body: { strategy_id: string; score: number; priority: FeedbackPriority; note?: string } = {
        strategy_id: strategy.id,
        score,
        priority,
      };
      if (note.trim() !== "") body.note = note.trim();
      await postStrategyFeedback(runId, body);
      setSaving("saved");
      onChanged();
      window.clearTimeout(savedTimer.current);
      savedTimer.current = window.setTimeout(() => setSaving("idle"), 2500);
    } catch (err) {
      setSaving("error");
      setServerError(err instanceof Error ? err.message : "Could not save feedback.");
    }
  }

  return (
    <div className="mt-3 rounded-md border border-stone-200 bg-white p-2.5 text-xs">
      {/* Advisory label — MUST be present */}
      <p className="mb-2 text-[11px] text-stone-400 italic">
        Advisory — affects how the next round spends attention, not current evidence.
      </p>

      {/* Priority buttons */}
      <div className="mb-2 flex flex-wrap gap-1.5">
        {PRIORITY_OPTIONS.map(({ value, label, icon: Icon, activeClass }) => (
          <button
            key={value}
            type="button"
            onClick={() => setPriority(value)}
            className={cn(
              "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-colors",
              priority === value
                ? activeClass
                : "border-stone-200 bg-white text-stone-500 hover:border-stone-300 hover:bg-stone-50",
            )}
            aria-pressed={priority === value}
          >
            <Icon className="h-3 w-3" aria-hidden />
            {label}
          </button>
        ))}
      </div>

      {/* Score slider */}
      <div className="mb-2 flex items-center gap-2">
        <label className="shrink-0 text-[11px] font-medium text-stone-500">
          Score
        </label>
        <input
          type="range"
          min={0}
          max={100}
          value={score}
          onChange={(e) => setScore(Number(e.target.value))}
          className="flex-1 accent-stone-500"
          aria-label="Advisory score 0 to 100"
        />
        <span className="w-7 text-right text-[11px] tabular-nums text-stone-600">
          {score}
        </span>
      </div>

      {/* Note */}
      <input
        type="text"
        value={note}
        maxLength={200}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Optional one-line note…"
        className="mb-2 w-full rounded border border-stone-200 bg-stone-50 px-2 py-1 text-[11px] text-stone-700 placeholder:text-stone-400 focus:border-stone-400 focus:outline-none"
        aria-label="Optional feedback note"
      />

      {/* Save button + status chip. The saved value itself is shown once, in
          the time-allocation ledger row ("Your score / priority"); here we only
          surface unsaved/saving/saved/error transitions. */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => void handleSave()}
          disabled={saving === "saving" || !dirty}
          className="h-6 px-2.5 text-[11px]"
        >
          {saving === "saving" && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
          {saving === "saving" ? "Saving…" : "Save feedback"}
        </Button>
        {dirty && saving !== "saving" && (
          <span className="text-[11px] text-amber-700">unsaved changes</span>
        )}
        {!dirty && saving === "idle" && saved && (
          <span className="text-[11px] text-stone-400">saved</span>
        )}
        {saving === "saved" && (
          <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
            saved
          </span>
        )}
        {saving === "error" && serverError && (
          <span className="inline-flex items-start gap-1 rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 text-[11px] leading-4 text-rose-700">
            {serverError}
          </span>
        )}
      </div>
    </div>
  );
}

function isFeedbackPriority(value: string | undefined): value is FeedbackPriority {
  return value === "focus" || value === "normal" || value === "reduce" || value === "repair";
}

// ─── StrategyCard ─────────────────────────────────────────────────────────────

function StrategyCard({
  strategy,
  runId,
  onChanged,
  ledger,
}: {
  strategy: StrategyView;
  runId: string;
  onChanged: () => void;
  ledger?: LedgerContext;
}) {
  const [expanded, setExpanded] = useState(false);
  const job = useJob();
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return (
    <Card
      className={cn(
        // a smooth, layout-stable selection emphasis (no jump/reflow).
        "transition-[box-shadow,border-color] duration-200",
        strategy.selected && "border-sage-300 ring-1 ring-sage-200",
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full items-start gap-1.5 px-3 py-2.5 text-left hover:bg-stone-50"
      >
        <Chevron className="mt-1 h-3.5 w-3.5 shrink-0 text-stone-400" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-sm font-semibold text-stone-800"><MathText>{strategy.title}</MathText></span>
            {strategy.selected && (
              <FadeIn>
                <Badge className="border-sage-200 bg-sage-50 text-sage-800">
                  <Route className="h-3 w-3" aria-hidden />
                  selected route
                </Badge>
              </FadeIn>
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
              <MathText>{strategy.one_line}</MathText>
            </span>
          )}
        </span>
      </button>
      <Collapse open={expanded}>
        <div className="border-t border-stone-100 px-3 py-2 pl-8 text-xs leading-relaxed text-stone-700">
          {strategy.status_sentence !== "" && (
            <p className="mb-1.5"><MathText>{strategy.status_sentence}</MathText></p>
          )}
          {strategy.main_risk !== "" && (
            <p className="mb-1.5">
              <span className="font-semibold text-stone-500">Main risk: </span>
              <MathText>{strategy.main_risk}</MathText>
            </p>
          )}
          {strategy.next_step !== "" && (
            <p className="mb-1.5">
              <span className="font-semibold text-stone-500">Next step: </span>
              <MathText>{strategy.next_step}</MathText>
            </p>
          )}

          {/* Rating block */}
          <RatingBlock strategy={strategy} runId={runId} onChanged={onChanged} />

          {/* Evidence + Time-allocation ledgers */}
          {ledger && (
            <>
              <EvidenceLedger ledger={ledger} strategy={strategy} />
              <TimeAllocationLedger ledger={ledger} strategy={strategy} />
            </>
          )}
        </div>
      </Collapse>
      <div className="flex flex-wrap items-center gap-2 border-t border-stone-100 px-3 py-1.5 pl-8">
        <Button
          size="sm"
          variant="outline"
          disabled={job.busy}
          onClick={() =>
            job.start(
              // send an effort the kernel actually allows for this route.
              () => continueRoute(runId, strategy.id, strategy.allowed_efforts?.[0]),
              () => onChanged(),
            )
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
  // a running agent shows a live elapsed timer so it never feels frozen.
  const liveElapsed = useElapsed(activity === "active");
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
      {activity === "active" ? (
        <span className="text-[11px] tabular-nums text-amber-700" aria-live="polite">
          {formatDuration(liveElapsed)}
        </span>
      ) : agent.duration_seconds !== null ? (
        <span className="text-[11px] tabular-nums text-stone-400">
          {formatDuration(agent.duration_seconds)}
        </span>
      ) : agent.last_updated ? (
        <span className="text-[11px] text-stone-400">{timeAgo(agent.last_updated, Date.now())}</span>
      ) : null}
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
 * CENTER workspace: claim card, strategy board, agent board (Goal 43B/C).
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
  const ledgerContext = strat.data?.ledger_context;
  const latestRound = strat.data?.latest_round;

  return (
    <div className="flex flex-col gap-4 p-4">
      <ClaimCard claim={claim.data?.claim} missing={claim.data?.missing ?? true} />

      {/* Latest round — shown above strategy board when present */}
      <RoundLedger round={latestRound} />

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
            <StrategyCard
              key={s.id}
              strategy={s}
              runId={runId}
              onChanged={onChanged}
              ledger={ledgerContext}
            />
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

      <ChatPanel runId={runId} selectedRoute={status?.selected_route ?? null} />
    </div>
  );
}
