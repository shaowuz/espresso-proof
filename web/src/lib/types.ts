/**
 * Types mirroring the Python server's cockpit view model.
 *
 * All trust labels and tones are SERVER-computed. The client renders the
 * typed display fields verbatim and must never derive trust from raw
 * statuses. `evidence_html` / `topbar_html` exist on the wire but are
 * intentionally never rendered (raw HTML is banned in the React app).
 */

export type Tone = "earned" | "warn" | "human" | "muted" | "neutral";

export interface ClaimView {
  text: string;
  status_sentence: string;
  human_review_required: boolean;
}

export interface AgentView {
  agent: string;
  title: string;
  status: string;
  status_word: string;
  last_updated: string | null;
  duration_seconds: number | null;
}

export interface StatusView {
  state: string;
  proof_status: string;
  completed: boolean;
  routes_preliminary: boolean;
  selected_route: { id: string; title: string } | null;
  evidence: { reference_supported_steps: number; machine_checks: number };
  budget: Record<string, unknown>;
  agents: AgentView[];
  run_id?: string;
  /** Present on the wire; NEVER rendered. */
  evidence_html?: string;
  /** Present on the wire; NEVER rendered. */
  topbar_html?: string;
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
}

export interface CounterexampleView {
  id: string;
  example: string;
  explanation: string;
  phrase: string;
  admissibility: string;
  repair: string;
  tone: Tone;
}

export interface ComputationView {
  id: string;
  quantity: string;
  method: string;
  result: string;
  phrase: string;
  tone: Tone;
}

export interface LiteratureView {
  id: string;
  title: string;
  authors: string[];
  locator: { kind: string; value: string };
  relevance: string;
  why: string;
  phrase: string;
  tone: Tone;
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
  strategies: StrategyView[];
  examples: ExampleView[];
  counterexamples: CounterexampleView[];
  computations: ComputationView[];
  literature: LiteratureView[];
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
 * Goal 43B: per-panel slices (each panel refreshes independently).
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
  | "literature";

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
}

export interface ClaimPanel extends PanelBase {
  panel: "claim";
  claim: ClaimView;
}
export interface StrategyPanel extends PanelBase {
  panel: "strategies";
  strategies: StrategyView[];
  ledger_context?: LedgerContext;
  latest_round?: LatestRound | null;
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
