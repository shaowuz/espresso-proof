import { useCallback, useEffect, useState } from "react";
import { BookOpen, FlaskConical, MessageSquare } from "lucide-react";
import { fetchRuns } from "./lib/api";
import type { RunListItem } from "./lib/types";
import { useSnapshot } from "./hooks/useSnapshot";
import { Header } from "./components/Header";
import { Composer } from "./components/Composer";
import { EvidenceRail } from "./components/EvidenceRail";
import { Workspace } from "./components/Workspace";
import { LiteratureRail } from "./components/LiteratureRail";
import { cn } from "./lib/cn";

type Pane = "evidence" | "workspace" | "literature";

/**
 * The one-page cockpit shell: header, three work zones, bottom composer.
 * Desktop: three columns, each independently scrollable. Narrow: tabbed
 * single column. The shell renders server-computed display copy only.
 */
export default function App() {
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [pane, setPane] = useState<Pane>("workspace");
  const { snapshot, error, refresh } = useSnapshot(selectedRunId);

  const loadRuns = useCallback(async (selectLatest: boolean) => {
    try {
      const list = await fetchRuns();
      setRuns(list);
      if (selectLatest && list.length > 0) {
        setSelectedRunId((current) => current ?? list[list.length - 1].run_id);
      }
    } catch {
      /* run list is a convenience; the empty state handles its absence */
    }
  }, []);

  useEffect(() => {
    void loadRuns(true);
  }, [loadRuns]);

  const onRunCreated = useCallback(
    (runId: string) => {
      setSelectedRunId(runId);
      void loadRuns(false);
    },
    [loadRuns],
  );

  return (
    <div className="flex h-screen flex-col bg-stone-50 text-stone-800">
      <Header
        snapshot={snapshot}
        runs={runs}
        selectedRunId={selectedRunId}
        onSelectRun={setSelectedRunId}
      />

      {error !== null && (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-xs text-amber-800">
          {error}
        </div>
      )}

      {/* narrow-screen pane tabs */}
      <nav className="flex shrink-0 border-b border-stone-200 bg-white lg:hidden">
        {(
          [
            ["evidence", "Evidence", FlaskConical],
            ["workspace", "Workspace", MessageSquare],
            ["literature", "Literature", BookOpen],
          ] as const
        ).map(([key, label, Icon]) => (
          <button
            key={key}
            type="button"
            onClick={() => setPane(key)}
            className={cn(
              "flex flex-1 items-center justify-center gap-1.5 px-2 py-2 text-xs font-medium",
              pane === key
                ? "border-b-2 border-sage-600 text-stone-800"
                : "text-stone-500",
            )}
          >
            <Icon className="h-3.5 w-3.5" aria-hidden /> {label}
          </button>
        ))}
      </nav>

      <main className="grid min-h-0 flex-1 lg:grid-cols-[minmax(280px,22%)_1fr_minmax(300px,26%)]">
        <aside
          className={cn(
            "min-h-0 overflow-y-auto border-stone-200 lg:block lg:border-r",
            pane === "evidence" ? "block" : "hidden",
          )}
        >
          {snapshot ? <EvidenceRail snapshot={snapshot} /> : <ShellPlaceholder />}
        </aside>

        <section
          className={cn(
            "min-h-0 overflow-y-auto lg:block",
            pane === "workspace" ? "block" : "hidden",
          )}
        >
          {snapshot ? (
            <Workspace snapshot={snapshot} onChanged={refresh} />
          ) : (
            <EmptyWorkspace hasRuns={runs.length > 0} />
          )}
        </section>

        <aside
          className={cn(
            "min-h-0 overflow-y-auto border-stone-200 lg:block lg:border-l",
            pane === "literature" ? "block" : "hidden",
          )}
        >
          {snapshot ? <LiteratureRail snapshot={snapshot} /> : <ShellPlaceholder />}
        </aside>
      </main>

      <Composer onRunCreated={onRunCreated} />
    </div>
  );
}

function ShellPlaceholder() {
  return (
    <div className="p-4 text-xs text-stone-400">
      This panel fills in once a run is selected.
    </div>
  );
}

function EmptyWorkspace({ hasRuns }: { hasRuns: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-8">
      <div className="w-full max-w-2xl rounded-xl border border-stone-200 bg-white p-6">
        <h1 className="text-base font-semibold text-stone-800">
          Mathematical research cockpit
        </h1>
        <p className="mt-1 text-sm text-stone-600">
          {hasRuns
            ? "Pick a run from the header, or state a new claim below."
            : "State a mathematical claim in the composer below to start."}
        </p>
        <ul className="mt-4 grid gap-2 text-[13px] text-stone-600 sm:grid-cols-3">
          <li className="rounded-lg border border-stone-100 bg-stone-50 p-3">
            <span className="font-medium text-stone-700">Left:</span> examples,
            counterexamples, and computation checks.
          </li>
          <li className="rounded-lg border border-stone-100 bg-stone-50 p-3">
            <span className="font-medium text-stone-700">Center:</span> the claim,
            proof strategies, and agent actions.
          </li>
          <li className="rounded-lg border border-stone-100 bg-stone-50 p-3">
            <span className="font-medium text-stone-700">Right:</span> literature,
            theorem locators, and source gaps.
          </li>
        </ul>
      </div>
    </div>
  );
}
