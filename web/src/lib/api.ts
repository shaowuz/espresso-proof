import type {
  AgentState,
  ClaimView,
  FeedbackPriority,
  JobInfo,
  LedgerContext,
  LatestRound,
  PanelBase,
  PanelName,
  RunListItem,
  Snapshot,
  StatusView,
  StrategyView,
} from "./types";

/**
 * Plain-fetch API client. Same origin as the Python server in production
 * (the built dist/ is served at /); the Vite dev server proxies /api.
 *
 * Errors surface as Error objects whose message is the server's friendly
 * "error" string when available — never a traceback.
 */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new Error("Could not reach the server.");
  }
  if (!res.ok) {
    let message = `The server could not handle this request (HTTP ${res.status}).`;
    try {
      const body: unknown = await res.json();
      if (body !== null && typeof body === "object") {
        const maybe = body as { error?: unknown; detail?: unknown };
        if (typeof maybe.error === "string" && maybe.error.trim() !== "") {
          message = maybe.error;
        } else if (typeof maybe.detail === "string" && maybe.detail.trim() !== "") {
          message = maybe.detail;
        }
      }
    } catch {
      /* keep the generic message */
    }
    throw new Error(message);
  }
  return (await res.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** GET /api/runs — most recent last; parsed defensively. */
export async function fetchRuns(): Promise<RunListItem[]> {
  const data = await request<{ runs?: unknown }>("/api/runs");
  if (!Array.isArray(data.runs)) return [];
  const runs: RunListItem[] = [];
  for (const item of data.runs) {
    if (item !== null && typeof item === "object") {
      const raw = item as { run_id?: unknown; statement?: unknown; claim?: unknown; project?: unknown };
      if (typeof raw.run_id === "string" && raw.run_id !== "") {
        runs.push({
          run_id: raw.run_id,
          statement: typeof raw.statement === "string" ? raw.statement : undefined,
          claim: typeof raw.claim === "string" ? raw.claim : undefined,
          project: typeof raw.project === "string" ? raw.project : undefined,
        });
      }
    }
  }
  return runs;
}

const EMPTY_MISSING: Snapshot["missing"] = {
  claim: true,
  grade: true,
  strategies: true,
  examples: true,
  counterexamples: true,
  computations: true,
  literature: true,
};

/** GET /api/runs/{id}/snapshot — light normalization so panels never crash. */
export async function fetchSnapshot(runId: string): Promise<Snapshot> {
  const raw = await request<Partial<Snapshot>>(
    `/api/runs/${encodeURIComponent(runId)}/snapshot`,
  );
  const status = raw.status;
  return {
    run_id: raw.run_id ?? runId,
    claim: raw.claim ?? {
      text: "",
      status_sentence: "",
      human_review_required: false,
    },
    status: {
      state: status?.state ?? "",
      proof_status: status?.proof_status ?? "",
      completed: status?.completed ?? false,
      routes_preliminary: status?.routes_preliminary ?? false,
      selected_route: status?.selected_route ?? null,
      evidence: status?.evidence ?? {
        reference_supported_steps: 0,
        machine_checks: 0,
      },
      budget: status?.budget ?? {},
      agents: Array.isArray(status?.agents) ? status.agents : [],
    },
    strategies: Array.isArray(raw.strategies) ? raw.strategies : [],
    examples: Array.isArray(raw.examples) ? raw.examples : [],
    counterexamples: Array.isArray(raw.counterexamples) ? raw.counterexamples : [],
    computations: Array.isArray(raw.computations) ? raw.computations : [],
    literature: Array.isArray(raw.literature) ? raw.literature : [],
    source_gaps: Array.isArray(raw.source_gaps) ? raw.source_gaps : [],
    missing: raw.missing ?? EMPTY_MISSING,
  };
}

/** POST /api/runs → 202 {"job_id"}
 *
 * Product cockpit runs use the real external agent path by default. The
 * deterministic stub path remains available to backend tests and smoke
 * harnesses, but it is not exposed from the UI because it produces template
 * artifacts rather than useful mathematical work.
 */
export function createRun(text: string, mode = "codex"): Promise<{ job_id: string }> {
  return post<{ job_id: string }>("/api/runs", { text, mode });
}

/** POST /api/runs/{id}/agents/{agent} → 202 {"job_id"} */
export function runAgent(runId: string, agent: string): Promise<{ job_id: string }> {
  return post<{ job_id: string }>(
    `/api/runs/${encodeURIComponent(runId)}/agents/${encodeURIComponent(agent)}`,
    {},
  );
}

/** POST /api/runs/{id}/continue → 202 {"job_id"} */
export function continueRoute(
  runId: string,
  strategyId: string,
  effort?: string,
): Promise<{ job_id: string }> {
  return post<{ job_id: string }>(
    `/api/runs/${encodeURIComponent(runId)}/continue`,
    { strategy: strategyId, effort: effort ?? "normal_pass" },
  );
}

/** POST /api/runs/{id}/strategy-feedback → 200 {ok, strategy_id, feedback} */
export function postStrategyFeedback(
  runId: string,
  feedback: { strategy_id: string; score: number; priority: FeedbackPriority; note?: string },
): Promise<{ ok: boolean; strategy_id: string; feedback: { score: number; priority: string; note: string } | null }> {
  return post(`/api/runs/${encodeURIComponent(runId)}/strategy-feedback`, feedback);
}

/** GET /api/jobs/{job_id} */
export function fetchJob(jobId: string): Promise<JobInfo> {
  return request<JobInfo>(`/api/jobs/${encodeURIComponent(jobId)}`);
}

/**
 * GET /api/runs/{id}/status — the fast-core header/agent state. Drops the
 * never-rendered HTML fields and normalizes defensively.
 */
export async function fetchStatus(runId: string): Promise<StatusView> {
  const status = await request<Partial<StatusView>>(
    `/api/runs/${encodeURIComponent(runId)}/status`,
  );
  return {
    state: status.state ?? "",
    proof_status: status.proof_status ?? "",
    completed: status.completed ?? false,
    routes_preliminary: status.routes_preliminary ?? false,
    selected_route: status.selected_route ?? null,
    evidence: status.evidence ?? { reference_supported_steps: 0, machine_checks: 0 },
    budget: status.budget ?? {},
    agents: Array.isArray(status.agents) ? status.agents : [],
    run_id: status.run_id ?? runId,
  };
}

/**
 * GET /api/runs/{id}/panel/{name} — one validated panel slice. The caller
 * passes the expected panel; the response is normalized so a panel never
 * crashes the page, and a missing artifact stays an honest empty/not_run slice.
 */
export async function fetchPanel<T extends PanelBase>(
  runId: string,
  panel: PanelName,
): Promise<T> {
  const raw = await request<Record<string, unknown>>(
    `/api/runs/${encodeURIComponent(runId)}/panel/${encodeURIComponent(panel)}`,
  );
  const base = {
    run_id: typeof raw.run_id === "string" ? raw.run_id : runId,
    panel,
    missing: raw.missing !== false,
    agent_state: isAgentState(raw.agent_state) ? raw.agent_state : "not_run",
  };
  if (panel === "claim") {
    const claim = raw.claim as ClaimView | undefined;
    return {
      ...base,
      claim: claim ?? { text: "", status_sentence: "", human_review_required: false },
    } as unknown as T;
  }
  if (panel === "strategies") {
    return {
      ...base,
      strategies: Array.isArray(raw.strategies) ? (raw.strategies as StrategyView[]) : [],
      ledger_context: raw.ledger_context as LedgerContext | undefined,
      latest_round: raw.latest_round as LatestRound | null | undefined,
    } as unknown as T;
  }
  const items = Array.isArray(raw.items) ? raw.items : [];
  if (panel === "literature") {
    return {
      ...base,
      items,
      source_gaps: Array.isArray(raw.source_gaps) ? (raw.source_gaps as string[]) : [],
    } as unknown as T;
  }
  return { ...base, items } as unknown as T;
}

function isAgentState(value: unknown): value is AgentState {
  return (
    value === "ready" ||
    value === "not_run" ||
    value === "running" ||
    value === "queued" ||
    value === "failed" ||
    value === "skipped" ||
    value === "done"
  );
}

/** Friendly message for a query error (server copy when present). */
export function errorText(err: unknown): string {
  return err instanceof Error && err.message !== ""
    ? err.message
    : "This panel could not load.";
}

/**
 * POST /api/runs/{id}/research-note — bind one advisory note to a run object
 * (Goal 43F). Returns the binding; the note never changes any status.
 */
export function postResearchNote(
  runId: string,
  note: { target_kind: string; target_id: string; text: string },
): Promise<{ ok: boolean; target_kind: string; target_id: string }> {
  return post(`/api/runs/${encodeURIComponent(runId)}/research-note`, note);
}

/** POST /api/jobs/{job_id}/cancel — best-effort stop of a queued/running job. */
export function cancelJob(jobId: string): Promise<{ ok: boolean; state: string }> {
  return post(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {});
}
