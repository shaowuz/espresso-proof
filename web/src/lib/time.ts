import { useEffect, useState } from "react";

/**
 * Goal 43E: human-feeling progress. Pure, testable duration formatting plus a
 * live ticking hook so a running job shows elapsed time and never feels frozen.
 * Display only — never changes status.
 */

/** "3s", "1m 04s", "2h 05m" — compact, monotonic, never negative. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  if (m < 60) return `${m}m ${String(rem).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

/** "just now", "5s ago", "2m ago", "1h ago" from an ISO timestamp. */
export function timeAgo(iso: string | null | undefined, nowMs: number): string {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const s = Math.max(0, Math.floor((nowMs - then) / 1000));
  if (s < 3) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

/**
 * A live elapsed-seconds counter that ticks every second while `active`. Used
 * to keep a running job's chip alive. Stops (and stops re-rendering) when the
 * job is no longer active.
 */
export function useElapsed(active: boolean): number {
  const [, setTick] = useState(0);
  const [startMs] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, [active]);
  return active ? Math.max(0, (Date.now() - startMs) / 1000) : 0;
}
