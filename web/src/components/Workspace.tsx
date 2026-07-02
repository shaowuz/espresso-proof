import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Code2,
  Eye,
  Loader2,
  Minus,
  Play,
  Route,
  ShieldAlert,
  Target,
  UserRound,
  Wrench,
  X,
} from "lucide-react";
import type {
  ActiveObject,
  ClaimView,
  FeedbackPriority,
  LatestRound,
  StatusView,
  StrategyView,
  StrategyPanel,
  StrategySummary,
} from "../lib/types";
import { continueRoute, errorText, postStrategyFeedback } from "../lib/api";
import { useClaimPanel, useStrategiesPanel, useTracePanel } from "../hooks/useCockpit";
import { useJob } from "../hooks/useJob";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { ToneBadge } from "./ToneBadge";
import { JobChip } from "./JobChip";
import { PanelPlaceholder } from "./ExpandableCard";
import { PanelSection } from "./PanelSection";
import { cn } from "../lib/cn";
import { MathText } from "./MathText";
import { AnimatedList, Collapse, FadeIn } from "./motion";
import { ChatPanel, type ChatMessage } from "./ChatPanel";
import { analyzeClaimText, type ClaimTextWarning } from "../lib/claimText";
import { AgentFlowTrace } from "./AgentFlowTrace";

const CLAIM_WARNING_LABELS: Record<ClaimTextWarning, string> = {
  unsafe_markup: "Unsafe markup hidden",
  html_like_markup: "Raw markup hidden",
  javascript_url: "JavaScript URL hidden",
  dangerous_tex_command: "Unsafe TeX command hidden",
  malformed_tex: "Malformed TeX hidden",
  truncated: "Raw input summarized",
};

function analyzeClaimForDisplay(claim: ClaimView) {
  const rawText = claim.raw_text ?? claim.text;
  const rawAnalysis = analyzeClaimText(rawText);
  if (!claim.raw_text) return rawAnalysis;
  const cleanAnalysis = analyzeClaimText(claim.text);
  const summary = claim.text.trim() === "" ? rawAnalysis.summary : cleanAnalysis.summary;
  const warnings = [...new Set([...rawAnalysis.warnings, ...cleanAnalysis.warnings])];
  return {
    ...rawAnalysis,
    summary,
    warnings,
    hasUnsafeMarkup: rawAnalysis.hasUnsafeMarkup || cleanAnalysis.hasUnsafeMarkup,
    hasMalformedTex: rawAnalysis.hasMalformedTex || cleanAnalysis.hasMalformedTex,
    isTruncated: rawAnalysis.isTruncated || cleanAnalysis.isTruncated,
  };
}

export function ClaimCard({ claim, missing }: { claim: ClaimView | undefined; missing: boolean }) {
  const [rawOpen, setRawOpen] = useState(false);
  const rawText = claim?.raw_text ?? "";
  if (missing || !claim || (claim.text === "" && rawText === "")) {
    return (
      <Card>
        <CardContent className="pt-2.5">
          <PanelPlaceholder text="Not run yet — the claim has not been parsed." />
        </CardContent>
      </Card>
    );
  }
  const analysis = analyzeClaimForDisplay(claim);
  const rawDiffers = analysis.raw !== analysis.summary;
  const showRawControl = rawDiffers || analysis.warnings.length > 0;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <CardTitle className="text-[17px] leading-relaxed">
              <MathText>{analysis.summary}</MathText>
            </CardTitle>
            {analysis.warnings.length > 0 && (
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {analysis.warnings.map((warning) => (
                  <Badge
                    key={warning}
                    className="border-amber-200 bg-amber-50 text-amber-900"
                  >
                    <ShieldAlert className="h-3 w-3" aria-hidden />
                    {CLAIM_WARNING_LABELS[warning]}
                  </Badge>
                ))}
              </div>
            )}
          </div>
          {claim.human_review_required && (
            <Badge className="shrink-0 border-violet-200 bg-violet-50 text-violet-800">
              <UserRound className="h-3 w-3" aria-hidden />
              Human review required
            </Badge>
          )}
        </div>
        {analysis.warnings.length > 0 && (
          <p className="mt-2 text-[14px] leading-relaxed text-amber-800">
            Unsafe or malformed markup was hidden from the main claim view.
          </p>
        )}
        {showRawControl && (
          <div className="mt-3 rounded-lg border border-stone-200 bg-stone-50/70">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRawOpen((open) => !open)}
              aria-expanded={rawOpen}
              className="w-full justify-between rounded-lg px-2.5 text-stone-700"
            >
              <span className="inline-flex items-center gap-1.5">
                <Eye className="h-3.5 w-3.5" aria-hidden />
                {rawOpen ? "Hide raw input" : "Show raw input"}
              </span>
              <ChevronDown
                className={cn(
                  "h-3.5 w-3.5 text-stone-400 transition-transform",
                  rawOpen && "rotate-180",
                )}
                aria-hidden
              />
            </Button>
            <Collapse open={rawOpen}>
              <div className="border-t border-stone-200 p-3">
                <p className="mb-1.5 inline-flex items-center gap-1 text-[13px] font-medium text-stone-500">
                  <Code2 className="h-3.5 w-3.5" aria-hidden />
                  Raw input
                </p>
                <pre className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-amber-100 bg-amber-50/60 p-2.5 font-mono text-[13px] leading-relaxed text-stone-700">
                  {analysis.raw}
                </pre>
              </div>
            </Collapse>
          </div>
        )}
      </CardHeader>
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
    <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-[13px] leading-relaxed">
      <p className="mb-1.5 font-semibold text-slate-600 text-[14px]">Latest round</p>
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
      <p className="mt-1.5 text-[12px] text-slate-400 italic">explanatory — not evidence</p>
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
  const queryClient = useQueryClient();
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
    const queryKey = ["panel", runId, "strategies"] as const;
    const optimisticFeedback = { score, priority, note: note.trim() };
    const previous = queryClient.getQueryData<StrategyPanel>(queryKey);
    queryClient.setQueryData<StrategyPanel>(queryKey, (current) =>
      current
        ? {
            ...current,
            strategies: current.strategies.map((item) =>
              item.id === strategy.id
                ? { ...item, my_feedback: optimisticFeedback, score }
                : item,
            ),
          }
        : current,
    );
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
      queryClient.setQueryData(queryKey, previous);
      setSaving("error");
      setServerError(err instanceof Error ? err.message : "Could not save feedback.");
    }
  }

  return (
    <div className="mt-3 rounded-md border border-stone-200 bg-white p-2.5 text-[14px]">
      {/* Advisory label — MUST be present */}
      <p className="mb-2 text-[13px] text-stone-400 italic">
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
              "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[13px] font-medium transition-colors",
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
        <label className="shrink-0 text-[13px] font-medium text-stone-500">
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
        <span className="w-7 text-right text-[13px] tabular-nums text-stone-600">
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
        className="mb-2 w-full rounded border border-stone-200 bg-stone-50 px-2 py-1 text-[13px] text-stone-700 placeholder:text-stone-400 focus:border-stone-400 focus:outline-none"
        aria-label="Optional feedback note"
      />

      <div className="mb-2 rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 text-[13px] leading-relaxed text-slate-600">
        <span className="font-medium text-slate-700">Next-round attention:</span>{" "}
        score {score} / {priority}
        {dirty ? " (draft, will affect next round after save)" : " (saved for next round)"}
      </div>

      {/* Save button + status chip. The saved value itself is shown once, in
          the time-allocation ledger row ("Your score / priority"); here we only
          surface unsaved/saving/saved/error transitions. */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => void handleSave()}
          disabled={saving === "saving" || !dirty}
          className="h-6 px-2.5 text-[13px]"
        >
          {saving === "saving" && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
          {saving === "saving" ? "Saving…" : "Save feedback"}
        </Button>
        {dirty && saving !== "saving" && (
          <span className="text-[13px] text-amber-700">will affect next round</span>
        )}
        {saving === "saving" && (
          <span className="text-[13px] text-amber-700">
            saving advisory preference
          </span>
        )}
        {!dirty && saving === "idle" && saved && (
          <span className="text-[13px] text-stone-400">saved for next round</span>
        )}
        {saving === "saved" && (
          <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-100 px-2 py-0.5 text-[13px] text-slate-600">
            saved for next round
          </span>
        )}
        {saving === "error" && serverError && (
          <span className="inline-flex items-start gap-1 rounded-md border border-rose-200 bg-rose-50 px-2 py-0.5 text-[13px] leading-4 text-rose-700">
            could not save, evidence unchanged — {serverError}
          </span>
        )}
      </div>
    </div>
  );
}

function isFeedbackPriority(value: string | undefined): value is FeedbackPriority {
  return value === "focus" || value === "normal" || value === "reduce" || value === "repair";
}

function ActiveObjectBanner({
  activeObject,
  onClear,
}: {
  activeObject: ActiveObject;
  onClear: () => void;
}) {
  if (!activeObject) return null;
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-indigo-200 bg-indigo-50/70 px-3 py-2 text-[14px] text-indigo-900">
      <p className="min-w-0">
        <span className="font-semibold capitalize">Active {activeObject.kind}: </span>
        <span className="break-words">
          <MathText>{activeObject.title}</MathText>
        </span>
      </p>
      {activeObject.kind === "strategy" && activeObject.source_phrase && (
        <ToneBadge tone={activeObject.source_tone ?? "neutral"} phrase={activeObject.source_phrase} />
      )}
      <Button
        size="sm"
        variant="ghost"
        onClick={onClear}
        className="h-6 shrink-0 px-2 text-indigo-800 hover:bg-indigo-100"
      >
        <X className="h-3 w-3" aria-hidden />
        Clear focus
      </Button>
    </div>
  );
}

function StrategySynthesisCard({ summary }: { summary: StrategySummary | undefined }) {
  const [open, setOpen] = useState(false);
  if (!summary) return null;
  const detailCount =
    summary.reasons.length + summary.source_gaps.length + (summary.next_cheapest_test ? 1 : 0);
  return (
    <div className="rounded-lg border border-stone-200 bg-white px-3 py-2.5 text-[14px] leading-relaxed shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-semibold uppercase tracking-wide text-stone-500">
            Strategy synthesis
          </p>
          <p className="mt-1 text-stone-700">
            <MathText>{summary.note}</MathText>
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1.5">
          {summary.score !== null && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setOpen((value) => !value)}
              aria-expanded={open}
              className="h-6 rounded-full px-2 text-[13px]"
              title="Advisory grade details — not proof evidence"
            >
              score {summary.score}
              <ChevronDown
                className={cn("h-3 w-3 transition-transform", open && "rotate-180")}
                aria-hidden
              />
            </Button>
          )}
          {summary.status !== "" && <Badge>{summary.status}</Badge>}
        </div>
      </div>
      <Collapse open={open && detailCount > 0}>
        <div className="mt-2 border-t border-stone-100 pt-2 text-[13px] text-stone-600">
          {summary.reasons.length > 0 && (
            <div className="mb-2">
              <p className="font-semibold text-stone-500">Why score</p>
              <ul className="mt-1 list-disc space-y-1 pl-4">
                {summary.reasons.map((reason, index) => (
                  <li key={`reason-${index}`}><MathText>{reason}</MathText></li>
                ))}
              </ul>
            </div>
          )}
          {summary.source_gaps.length > 0 && (
            <div className="mb-2">
              <p className="font-semibold text-stone-500">Source gaps</p>
              <ul className="mt-1 list-disc space-y-1 pl-4 text-amber-800">
                {summary.source_gaps.map((gap, index) => (
                  <li key={`gap-${index}`}><MathText>{gap}</MathText></li>
                ))}
              </ul>
            </div>
          )}
          {summary.next_cheapest_test !== "" && (
            <p>
              <span className="font-semibold text-stone-500">Next cheapest test: </span>
              <MathText>{summary.next_cheapest_test}</MathText>
            </p>
          )}
          <p className="mt-2 text-[12px] italic text-stone-400">
            Advisory grade only; it does not create proof, source, checked, or refuted status.
          </p>
        </div>
      </Collapse>
    </div>
  );
}

// ─── StrategyCard ─────────────────────────────────────────────────────────────

function CompactResearchMemo({ memo }: { memo: string | undefined }) {
  const text = memo?.trim();
  if (!text) return null;
  return (
    <section className="rounded-md border border-stone-200 bg-white px-3 py-2 text-[14px] leading-relaxed">
      <div className="mb-1 text-[13px] font-semibold uppercase tracking-wide text-stone-500">
        Research memo
      </div>
      <div className="whitespace-pre-wrap text-stone-700">
        <MathText>{text}</MathText>
      </div>
    </section>
  );
}

export function StrategyCard({
  strategy,
  runId,
  onChanged,
  activeObject,
  onSetActiveObject,
  continueAvailable,
}: {
  strategy: StrategyView;
  runId: string;
  onChanged: () => void;
  activeObject: ActiveObject;
  onSetActiveObject: (activeObject: ActiveObject) => void;
  continueAvailable: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const job = useJob();
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const active = activeObject?.kind === "strategy" && activeObject.id === strategy.id;
  return (
    <Card
      className={cn(
        // a smooth, layout-stable selection emphasis (no jump/reflow).
        "transition-[box-shadow,border-color] duration-200",
        strategy.selected && "border-sage-300 ring-1 ring-sage-200",
        active && "border-indigo-300 ring-2 ring-indigo-200",
      )}
    >
      <button
        type="button"
        onClick={() => {
          onSetActiveObject({
            kind: "strategy",
            id: strategy.id,
            title: strategy.title,
            source_phrase: strategy.source_phrase,
            source_tone: strategy.source_tone,
          });
          setExpanded((v) => !v);
        }}
        aria-expanded={expanded}
        aria-pressed={active}
        className="flex w-full items-start gap-1.5 px-3 py-2.5 text-left hover:bg-stone-50"
      >
        <Chevron className="mt-1 h-3.5 w-3.5 shrink-0 text-stone-400" aria-hidden />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-1.5">
            <span className="text-[15px] font-semibold text-stone-800"><MathText>{strategy.title}</MathText></span>
            {active && (
              <FadeIn>
                <Badge className="border-indigo-200 bg-indigo-50 text-indigo-800">
                  <Target className="h-3 w-3" aria-hidden />
                  Active route
                </Badge>
              </FadeIn>
            )}
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
            <span className="mt-0.5 block text-[14px] leading-relaxed text-stone-600">
              <MathText>{strategy.one_line}</MathText>
            </span>
          )}
        </span>
      </button>
      <Collapse open={expanded}>
        <div className="border-t border-stone-100 px-3 py-2 pl-8 text-[14px] leading-relaxed text-stone-700">
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
        </div>
      </Collapse>
      <div className="flex flex-wrap items-center gap-2 border-t border-stone-100 px-3 py-1.5 pl-8">
        <Button
          size="sm"
          variant="outline"
          disabled={job.busy || !continueAvailable}
          title={
            continueAvailable
              ? undefined
              : "The initial pass is still running; Continue unlocks at your decision point."
          }
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
        {!continueAvailable && (
          <span className="text-[13px] text-stone-500">
            Unlocks at your decision point — the initial pass is still running.
          </span>
        )}
        <JobChip phase={job.phase} error={job.error} doneLabel="pass finished" />
      </div>
    </Card>
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
  chatMessages,
  chatLastNote,
  activeObject,
  onActiveObjectChange,
}: {
  runId: string;
  status: StatusView | undefined;
  onChanged: () => void;
  chatMessages: ChatMessage[];
  chatLastNote: string;
  activeObject: ActiveObject;
  onActiveObjectChange: (activeObject: ActiveObject) => void;
}) {
  const claim = useClaimPanel(runId);
  const strat = useStrategiesPanel(runId);
  const trace = useTracePanel(runId);
  const strategies = strat.data && !strat.data.missing ? strat.data.strategies : [];

  return (
    <div className="flex flex-col gap-4 p-4">
      <ClaimCard claim={claim.data?.claim} missing={claim.data?.missing ?? true} />
      <CompactResearchMemo memo={strat.data?.compact_memo} />
      <AgentFlowTrace
        trace={trace.data}
        isPending={trace.isPending}
        isError={trace.isError}
        errorText={errorText(trace.error)}
        onRetry={() => void trace.refetch()}
      />
      <ActiveObjectBanner
        activeObject={activeObject}
        onClear={() => onActiveObjectChange(null)}
      />

      {/* The latest-round ledger now lives in the bottom-left settings control
          (Codex-style minimal workspace); see SettingsButton. */}

      <PanelSection
        icon={Route}
        title="Strategies"
        download={{ runId, panel: "strategies", label: "strategies" }}
        count={strat.data && !strat.data.missing ? strategies.length : null}
        agentState={strat.data?.agent_state}
        agent={status?.agents.find((agent) => agent.agent === "claim_understanding")}
        isPending={strat.isPending}
        isRefetching={strat.isFetching}
        isError={strat.isError}
        errorText={errorText(strat.error)}
        hasData={strategies.length > 0}
        emptyText="Not run yet — no proof routes have been explored."
        onRetry={() => void strat.refetch()}
      >
        <div className="flex flex-col gap-2">
          <StrategySynthesisCard summary={strat.data?.strategy_summary} />
          {status?.routes_preliminary && (
            <Badge className="w-fit border-gray-200 bg-gray-100 text-gray-600">
              preliminary
            </Badge>
          )}
          <AnimatedList
            items={strategies}
            getKey={(strategy) => strategy.id}
            className="flex flex-col gap-2"
          >
            {(s) => (
              <StrategyCard
                strategy={s}
                runId={runId}
                onChanged={onChanged}
                activeObject={activeObject}
                onSetActiveObject={onActiveObjectChange}
                // absent on older servers → keep the button live (kernel still
                // rejects honestly; the chip shows its error).
                continueAvailable={status?.continue_available ?? true}
              />
            )}
          </AnimatedList>
        </div>
      </PanelSection>

      {/* The central agents board is gone — each evidence/literature panel now
          carries its own run/stop toggle in its header. */}

      {status?.completed && (
        <p className="flex items-center gap-1.5 text-[14px] text-stone-500">
          <CheckCircle2 className="h-3.5 w-3.5 text-stone-400" aria-hidden />
          This run has finished its budgeted passes. Conclusions above carry their own
          evidence labels.
        </p>
      )}

      <ChatPanel
        runId={runId}
        activeObject={activeObject}
        messages={chatMessages}
        lastNote={chatLastNote}
      />
    </div>
  );
}
