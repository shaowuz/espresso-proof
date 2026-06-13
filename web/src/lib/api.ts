import type { JobInfo, RunListItem, Snapshot } from "./types";

/**
 * Plain-fetch API client. Same origin as the Python server in production
 * (the built dist/ is served at /); the Vite dev server proxies /api.
 *
 * Errors surface as Error objects whose message is the server's friendly
 * "error" string when available — never a traceback.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
      const raw = item as { run_id?: unknown; claim?: unknown; project?: unknown };
      if (typeof raw.run_id === "string" && raw.run_id !== "") {
        runs.push({
          run_id: raw.run_id,
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

/** POST /api/runs → 202 {"job_id"} */
export function createRun(text: string, mode?: string): Promise<{ job_id: string }> {
  return post<{ job_id: string }>("/api/runs", mode ? { text, mode } : { text });
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
): Promise<{ job_id: string }> {
  return post<{ job_id: string }>(
    `/api/runs/${encodeURIComponent(runId)}/continue`,
    { strategy: strategyId, effort: "normal_pass" },
  );
}

/** GET /api/jobs/{job_id} */
export function fetchJob(jobId: string): Promise<JobInfo> {
  return request<JobInfo>(`/api/jobs/${encodeURIComponent(jobId)}`);
}
