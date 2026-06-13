import { useCallback, useEffect, useRef, useState } from "react";
import { fetchJob } from "../lib/api";

export type JobPhase = "idle" | "queued" | "running" | "done" | "failed";

const POLL_MS = 1000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Kick off a 202-style job and poll /api/jobs/{id} every second.
 * Phase flips to "queued" synchronously so every button gives visible
 * feedback well under 200ms.
 */
export function useJob(): {
  phase: JobPhase;
  error: string | null;
  busy: boolean;
  start: (
    kickoff: () => Promise<{ job_id: string }>,
    onDone?: (result?: { run_id?: string }) => void,
  ) => void;
  reset: () => void;
} {
  const [phase, setPhase] = useState<JobPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const aliveRef = useRef(true);
  const busyRef = useRef(false);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const start = useCallback(
    (
      kickoff: () => Promise<{ job_id: string }>,
      onDone?: (result?: { run_id?: string }) => void,
    ) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setPhase("queued");
      setError(null);
      void (async () => {
        try {
          const { job_id } = await kickoff();
          while (aliveRef.current) {
            await sleep(POLL_MS);
            if (!aliveRef.current) return;
            const job = await fetchJob(job_id);
            if (job.state === "done") {
              busyRef.current = false;
              setPhase("done");
              onDone?.(job.result);
              return;
            }
            if (job.state === "failed") {
              busyRef.current = false;
              setPhase("failed");
              setError(job.error ?? "The job failed.");
              return;
            }
            setPhase(job.state === "running" ? "running" : "queued");
          }
          busyRef.current = false;
        } catch (err) {
          busyRef.current = false;
          if (aliveRef.current) {
            setPhase("failed");
            setError(err instanceof Error ? err.message : "Request failed.");
          }
        }
      })();
    },
    [],
  );

  const reset = useCallback(() => {
    if (busyRef.current) return;
    setPhase("idle");
    setError(null);
  }, []);

  return {
    phase,
    error,
    busy: phase === "queued" || phase === "running",
    start,
    reset,
  };
}
