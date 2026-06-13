import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSnapshot } from "../lib/api";
import type { Snapshot } from "../lib/types";

const POLL_MS = 4000;

/**
 * Polls the selected run's snapshot every 4s, pausing while the document
 * is hidden and refreshing immediately when it becomes visible again.
 */
export function useSnapshot(runId: string | null): {
  snapshot: Snapshot | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
} {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const runRef = useRef(runId);
  runRef.current = runId;

  const refresh = useCallback(() => {
    const id = runRef.current;
    if (id === null) return;
    void fetchSnapshot(id)
      .then((snap) => {
        if (runRef.current === id) {
          setSnapshot(snap);
          setError(null);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (runRef.current === id) {
          setError(err instanceof Error ? err.message : "Could not load the run.");
          setLoading(false);
        }
      });
  }, []);

  useEffect(() => {
    if (runId === null) {
      setSnapshot(null);
      setError(null);
      setLoading(false);
      return;
    }
    setSnapshot(null);
    setError(null);
    setLoading(true);
    refresh();
    const tick = () => {
      if (!document.hidden) refresh();
    };
    const interval = window.setInterval(tick, POLL_MS);
    const onVisible = () => {
      if (!document.hidden) refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [runId, refresh]);

  return { snapshot, error, loading, refresh };
}
