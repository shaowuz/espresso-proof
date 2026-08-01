/**
 * Types mirroring the Python server's cockpit view model.
 *
 * All trust labels and tones are SERVER-computed. The client renders the
 * typed display fields verbatim and must never derive trust from raw
 * statuses. Raw HTML is banned in the React app.
 */

export type Tone = "earned" | "warn" | "human" | "muted" | "neutral";

export interface ClaimView {
  text: string;
  raw_text?: string;
  status_sentence: string;
  human_review_required: boolean;
}

export type ActiveObject =
  | { kind: "strategy"; id: string; title: string; source_phrase?: string; source_tone?: Tone }
  | { kind: "literature"; id: string; title: string }
  | { kind: "example"; id: string; title: string }
  | { kind: "counterexample"; id: string; title: string }
  | null;

export interface AgentView {
  agent: string;
  title: string;
  status: string;
  status_word: string;
  last_updated: string | null;
  duration_seconds: number | null;
}

export interface PressureLaneView {
  stage_id: string;
  agent: string;
  title: string;
  display_name: string;
  status: string;
  status_word: string;
  agent_status: string;
  default_visible: boolean;
  default_execution: string;
  counts_as_evidence: boolean;
  pending_counts_as_evidence: boolean;
  evidence_note: string;
  claim_clear_blocked: boolean;
}

/** Live dead-worker diagnostic: an agent call recorded `started` with no
 * terminal record and no in-memory job progressing it. Display only — carries
 * no mathematical status. */
export interface StalledInfo {
  stage: string;
  last_active_stage: string;
  since: string | null;
  elapsed_seconds: number | null;
  last_successful_stage: string | null;
  diagnostics: string[];
  message: string;
}

export interface StatusView {
  state: string;
  proof_status: string;
  /** True when the run is at or past the decision point, so a Continue would
   * be accepted by the kernel (absent on older servers → treat as true). */
  continue_available?: boolean;
  /** Non-null when the server detected a dead worker mid-call. */
  stalled?: StalledInfo | null;
  completed: boolean;
  routes_preliminary: boolean;
  selected_route: { id: string; title: string } | null;
  evidence: { reference_supported_steps: number; machine_checks: number };
  budget: Record<string, unknown>;
  agents: AgentView[];
  run_id?: string;
}

export interface StrategyView {
  id: string;
  title: string;
  one_line: string;
  score: number | null;
  status_sentence: string;
  source_phrase: string;
  source_tone: Tone;
  main_risk: string;
  next_step: string;
  selected: boolean;
  my_feedback: { score: number; priority: string; note: string } | null;
  /** Efforts the kernel allows for this route; the continue action sends one. */
  allowed_efforts: string[];
}

export interface LedgerContext {
  source_gaps: number;
  example_pressure: number;
  counterexample_pressure: number;
  reviewer_objections: string[];
  target_subgoal_id: string | null;
  target_local_claim: string;
  effort: string;
  next_cheapest_test: string;
}

export interface LatestRound {
  selected_strategy: string;
  target_subgoal_id: string | null;
  local_claim: string | null;
  effort: string;
  reviewer_blockers: string[];
  stop_reason: string;
  rounds_used: number | null;
  max_rounds: number | null;
  graph_changed: boolean;
  open_subgoals: number | null;
  advisory_only: true;
}

export type FeedbackPriority = "focus" | "normal" | "reduce" | "repair";

export interface ExampleView {
  id: string;
  name: string;
  tests: string[];
  behavior: string;
  admissibility: string;
  result_phrase: string;
  tone: Tone;
  related_strategy_ids?: string[];
  target_subgoal_id?: string | null;
  route_relevance?: "direct" | "pressure" | "background" | "unknown";
}

export interface CounterexampleView {
  id: string;
  example: string;
  explanation: string;
  phrase: string;
  admissibility: string;
  repair: string;
  tone: Tone;
  related_strategy_ids?: string[];
  target_subgoal_id?: string | null;
  route_relevance?: "direct" | "pressure" | "background" | "unknown";
}

export interface ComputationView {
  id: string;
  quantity: string;
  method: string;
  result: string;
  phrase: string;
  tone: Tone;
  related_strategy_ids?: string[];
  target_subgoal_id?: string | null;
  route_relevance?: "direct" | "pressure" | "background" | "unknown";
}

export interface LiteratureView {
  id: string;
  title: string;
  authors: string[];
  locator: { kind: string; value: string };
  relevance: string;
  why: string;
  /** A way IN to the source (recorded url or a scholar search) — display only. */
  url?: string;
  phrase: string;
  tone: Tone;
  related_strategy_ids?: string[];
  target_subgoal_id?: string | null;
  route_relevance?: "direct" | "pressure" | "background" | "unknown";
}

export interface StrategySummary {
  note: string;
  status: string;
  score: number | null;
  reasons: string[];
  source_gaps: string[];
  next_cheapest_test: string;
}

export type MissingKey =
  | "claim"
  | "grade"
  | "strategies"
  | "examples"
  | "counterexamples"
  | "computations"
  | "literature";

export interface Snapshot {
  run_id: string;
  claim: ClaimView;
  status: StatusView;
  compact_memo: string;
  strategies: StrategyView[];
  strategy_summary?: StrategySummary;
  examples: ExampleView[];
  counterexamples: CounterexampleView[];
  computations: ComputationView[];
  literature: LiteratureView[];
  pressure_lanes: PressureLaneView[];
  source_gaps: string[];
  missing: Record<MissingKey, boolean>;
}

/** Items from GET /api/runs — treated defensively (only run_id is trusted). */
export interface RunListItem {
  run_id: string;
  statement?: string;
  claim?: string;
  project?: string;
}

export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";

export interface JobInfo {
  state: JobState;
  error?: string;
  result?: { run_id?: string };
}

/**
 * Per-panel slices (each panel refreshes independently).
 *
 * ``agent_state`` is honest PROCESS state only — "ready" once the artifact is
 * present, "not_run" when neither artifact nor agent exist, and
 * running/queued/failed/skipped/done from the backing agent. It is never an
 * evidence signal; the tones inside each slice still come only from the
 * validated artifact status, so a "running" or cached panel cannot earn green.
 */
export type PanelName =
  | "claim"
  | "strategies"
  | "examples"
  | "counterexamples"
  | "computations"
  | "literature"
  | "trace";

export type AgentState =
  | "ready"
  | "not_run"
  | "running"
  | "queued"
  | "failed"
  | "skipped"
  | "done";

export interface PanelBase {
  run_id: string;
  panel: PanelName;
  missing: boolean;
  agent_state: AgentState;
  pressure_lane?: PressureLaneView | null;
}

export interface ClaimPanel extends PanelBase {
  panel: "claim";
  claim: ClaimView;
}
export interface StrategyPanel extends PanelBase {
  panel: "strategies";
  strategies: StrategyView[];
  strategy_summary?: StrategySummary;
  ledger_context?: LedgerContext;
  latest_round?: LatestRound | null;
  compact_memo?: string;
}
export interface ExamplesPanel extends PanelBase {
  panel: "examples";
  items: ExampleView[];
}
export interface CounterexamplesPanel extends PanelBase {
  panel: "counterexamples";
  items: CounterexampleView[];
}
export interface ComputationsPanel extends PanelBase {
  panel: "computations";
  items: ComputationView[];
}
export interface LiteraturePanel extends PanelBase {
  panel: "literature";
  items: LiteratureView[];
  source_gaps: string[];
}

export interface TraceItem {
  stage_id: string;
  display_name: string;
  state_word: string;
  tone: Tone;
  group: string;
  evidence_role_word: string;
  advisory_note: string;
  blocked_reason: string;
  status_detail: string;
  updated_at: string;
}

export interface TracePanel extends PanelBase {
  panel: "trace";
  items: TraceItem[];
  unavailable?: boolean;
  advisory_note?: string;
}
