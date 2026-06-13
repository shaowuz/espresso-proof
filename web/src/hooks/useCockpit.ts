import { useQuery } from "@tanstack/react-query";
import { fetchPanel, fetchStatus } from "../lib/api";
import { pollWhenVisible } from "../lib/queryClient";
import type {
  ClaimPanel,
  ComputationsPanel,
  CounterexamplesPanel,
  ExamplesPanel,
  LiteraturePanel,
  PanelBase,
  PanelName,
  StatusView,
  StrategyPanel,
} from "../lib/types";

const STATUS_POLL = 3000;
const FAST_POLL = 3000;
const EVIDENCE_POLL = 5000;

/**
 * One independent query per panel: examples loading never blocks strategies,
 * literature never blanks the page, and a slow sibling cannot stall route
 * selection. Each query keeps its last good slice during refetch and pauses
 * while the tab is hidden.
 */
export function useStatus(runId: string | null) {
  return useQuery<StatusView>({
    queryKey: ["status", runId],
    queryFn: () => fetchStatus(runId as string),
    enabled: runId !== null,
    refetchInterval: () => pollWhenVisible(STATUS_POLL),
  });
}

function usePanel<T extends PanelBase>(
  runId: string | null,
  panel: PanelName,
  intervalMs: number,
) {
  return useQuery<T>({
    queryKey: ["panel", runId, panel],
    queryFn: () => fetchPanel<T>(runId as string, panel),
    enabled: runId !== null,
    refetchInterval: () => pollWhenVisible(intervalMs),
  });
}

export const useClaimPanel = (runId: string | null) =>
  usePanel<ClaimPanel>(runId, "claim", FAST_POLL);
export const useStrategiesPanel = (runId: string | null) =>
  usePanel<StrategyPanel>(runId, "strategies", FAST_POLL);
export const useExamplesPanel = (runId: string | null) =>
  usePanel<ExamplesPanel>(runId, "examples", EVIDENCE_POLL);
export const useCounterexamplesPanel = (runId: string | null) =>
  usePanel<CounterexamplesPanel>(runId, "counterexamples", EVIDENCE_POLL);
export const useComputationsPanel = (runId: string | null) =>
  usePanel<ComputationsPanel>(runId, "computations", EVIDENCE_POLL);
export const useLiteraturePanel = (runId: string | null) =>
  usePanel<LiteraturePanel>(runId, "literature", EVIDENCE_POLL);
