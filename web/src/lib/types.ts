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
}

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

export type JobState = "queued" | "running" | "done" | "failed";

export interface JobInfo {
  state: JobState;
  error?: string;
  result?: { run_id?: string };
}
