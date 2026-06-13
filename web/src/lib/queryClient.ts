import { QueryClient } from "@tanstack/react-query";

/**
 * Display-only cache (Goal 43B): TanStack Query mirrors Espresso's
 * artifact model — each panel is its own query. The cache is never a source
 * of truth; every served slice was validated server-side, and a query error
 * surfaces the backend's friendly copy. Conservative polling; failed queries
 * keep the last good slice visible (the component shows the failure state).
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 2000,
      gcTime: 60_000,
      retry: 1,
      refetchOnWindowFocus: true,
      // keep the previous slice visible during a refetch so panels never blank
      placeholderData: (prev: unknown) => prev,
    },
  },
});

/** Pause polling while the tab is hidden; otherwise refresh on the interval. */
export function pollWhenVisible(ms: number): number | false {
  return document.hidden ? false : ms;
}
