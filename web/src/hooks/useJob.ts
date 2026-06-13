import { useCallback, useEffect, useRef, useState } from "react";
import { cancelJob, fetchJob } from "../lib/api";

export type JobPhase = "idle" | "queued" | "running" | "done" | "failed";

const POLL_MS = 1000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Kick off a 202-style job and poll /api/jobs/{id} every second.
 * Phase flips to "queued" synchronously so every button gives visible
 * feedback well under 200ms. `stop()` cancels the job (best-effort — a queued
 * job never runs; a running agent cannot be force-killed) and returns the UI
 * to idle immediately.
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
  stop: () => void;
} {
  const [phase, setPhase] = useState<JobPhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const aliveRef = useRef(true);
  const busyRef = useRef(false);
  const stoppedRef = useRef(false);
  const jobIdRef = useRef<string | null>(null);

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
      stoppedRef.current = false;
      jobIdRef.current = null;
      setPhase("queued");
      setError(null);
      void (async () => {
        try {
          const { job_id } = await kickoff();
          jobIdRef.current = job_id;
          while (aliveRef.current && !stoppedRef.current) {
            await sleep(POLL_MS);
            if (!aliveRef.current || stoppedRef.current) break;
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
            if (job.state === "cancelled") break;
            setPhase(job.state === "running" ? "running" : "queued");
          }
          busyRef.current = false;
        } catch (err) {
          busyRef.current = false;
          if (aliveRef.current && !stoppedRef.current) {
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

  const stop = useCallback(() => {
    stoppedRef.current = true;
    busyRef.current = false;
    setPhase("idle");
    setError(null);
    const id = jobIdRef.current;
    if (id) void cancelJob(id).catch(() => undefined);
  }, []);

  return {
    phase,
    error,
    busy: phase === "queued" || phase === "running",
    start,
    reset,
    stop,
  };
}
