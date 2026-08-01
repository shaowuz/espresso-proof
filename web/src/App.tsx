import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { BookOpen, FlaskConical, MessageSquare } from "lucide-react";
import { fetchChatLog, fetchRuns } from "./lib/api";
import { streamChat } from "./lib/chat";
import type { ActiveObject, RunListItem } from "./lib/types";
import { useClaimPanel, useStatus, useStrategiesPanel } from "./hooks/useCockpit";
import { Header } from "./components/Header";
import { Composer } from "./components/Composer";
import { SettingsButton } from "./components/SettingsButton";
import { EvidenceRail } from "./components/EvidenceRail";
import { Workspace } from "./components/Workspace";
import { InstantDraft } from "./components/InstantDraft";
import { LiteratureRail } from "./components/LiteratureRail";
import { cn } from "./lib/cn";
import type { ChatMessage } from "./components/ChatPanel";

type Pane = "evidence" | "workspace" | "literature";

/**
 * One-page cockpit shell. every panel is its own TanStack Query, so
 * the workspace opens on fast-core (claim/strategies/status) before the
 * evidence agents finish, and examples/counterexamples/literature refresh
 * independently. A Continue/Run pass invalidates only the affected queries.
 */
export default function App() {
  const queryClient = useQueryClient();
  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [pane, setPane] = useState<Pane>("workspace");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatLastNote, setChatLastNote] = useState("");
  const [chatStreaming, setChatStreaming] = useState(false);
  const [activeObject, setActiveObject] = useState<ActiveObject>(null);
  // The claim the user just submitted, captured the instant they hit
  // send so the instant draft streams immediately — before (and decoupled from)
  // run creation, so a slow workflow never blanks the page.
  const [pendingClaim, setPendingClaim] = useState("");
  const chatNextId = useRef(0);
  const selectedRunRef = useRef<string | null>(null);
  const activeSeededRunRef = useRef<string | null>(null);

  const status = useStatus(selectedRunId);
  const claim = useClaimPanel(selectedRunId);
  // shared (cached) strategies query — also feeds the bottom-left settings the
  // latest-round detail, so the main workspace can stay minimal.
  const strategies = useStrategiesPanel(selectedRunId);
  const hasRun = selectedRunId !== null;

  const loadRuns = useCallback(async () => {
    try {
      const list = await fetchRuns();
      setRuns(list);
    } catch {
      /* run list is a convenience; the empty state handles its absence */
    }
  }, []);

  // One page = one problem. The URL names the problem (?run=<id>); a bare URL
  // is a fresh workspace for a new one. The old behavior auto-selected the
  // latest run on load, which made "open a new page for a new problem"
  // impossible — every page snapped back to the last problem.
  useEffect(() => {
    void loadRuns();
    const fromUrl = new URLSearchParams(window.location.search).get("run");
    if (fromUrl) setSelectedRunId(fromUrl);
  }, [loadRuns]);

  useEffect(() => {
    const url = new URL(window.location.href);
    if (selectedRunId) url.searchParams.set("run", selectedRunId);
    else url.searchParams.delete("run");
    window.history.replaceState(null, "", url.toString());
  }, [selectedRunId]);

  useEffect(() => {
    selectedRunRef.current = selectedRunId;
    chatNextId.current = 0;
    setChatMessages([]);
    setChatLastNote("");
    setChatStreaming(false);
    setActiveObject(null);
    activeSeededRunRef.current = null;
    if (!selectedRunId) return;
    // Opening a problem RESUMES its conversation. The transcript is persisted
    // per run on the server; held only in browser memory it evaporated on
    // every reload.
    void fetchChatLog(selectedRunId).then((messages) => {
      if (selectedRunRef.current !== selectedRunId || messages.length === 0) return;
      const seeded = messages.map((m) => ({
        id: chatNextId.current++,
        role: m.role,
        text: m.text,
        activeObject: null,
      }));
      setChatMessages(seeded);
    });
  }, [selectedRunId]);

  useEffect(() => {
    if (!selectedRunId || !strategies.data || strategies.data.missing) return;
    const routeList = strategies.data.strategies;
    const selectedRoute = status.data?.selected_route ?? null;
    setActiveObject((current) => {
      if (current?.kind === "strategy") {
        const liveRoute = routeList.find((route) => route.id === current.id);
        return liveRoute
          ? {
              ...current,
              title: liveRoute.title,
              source_phrase: liveRoute.source_phrase,
              source_tone: liveRoute.source_tone,
            }
          : null;
      }
      if (current) return current;
      if (!selectedRoute) return null;
      if (activeSeededRunRef.current === selectedRunId) return null;
      const routeExists = routeList.some((route) => route.id === selectedRoute.id);
      if (!routeExists) return null;
      activeSeededRunRef.current = selectedRunId;
      const route = routeList.find((item) => item.id === selectedRoute.id);
      return {
        kind: "strategy",
        id: selectedRoute.id,
        title: route?.title ?? selectedRoute.title,
        source_phrase: route?.source_phrase,
        source_tone: route?.source_tone,
      };
    });
  }, [selectedRunId, status.data?.selected_route, strategies.data]);

  // refresh the affected queries after a pass — no full page reload.
  const refreshRun = useCallback(() => {
    if (selectedRunId === null) return;
    void queryClient.invalidateQueries({ queryKey: ["status", selectedRunId] });
    void queryClient.invalidateQueries({ queryKey: ["panel", selectedRunId] });
  }, [queryClient, selectedRunId]);

  const onRunCreated = useCallback(
    (runId: string) => {
      setSelectedRunId(runId);
      void loadRuns();
    },
    [loadRuns],
  );

  const appendChat = useCallback(
    (role: ChatMessage["role"], text: string, context: ActiveObject): number => {
      const id = chatNextId.current++;
      setChatMessages((current) => [...current, { id, role, text, activeObject: context }]);
      return id;
    },
    [],
  );

  const sendChat = useCallback(
    async (message: string) => {
      const runId = selectedRunId;
      const msg = message.trim();
      if (!runId || msg === "" || chatStreaming) return;
      const context = activeObject;
      setChatLastNote(msg);
      appendChat("you", msg, context);
      setChatStreaming(true);
      const replyId = appendChat("espresso", "", null);
      let any = false;
      await streamChat(
        runId,
        msg,
        context,
        (line) => {
          if (selectedRunRef.current !== runId) return;
          any = true;
          setChatMessages((current) =>
            current.map((item) =>
              item.id === replyId
                ? { ...item, text: item.text === "" ? line : `${item.text}\n${line}` }
                : item,
            ),
          );
        },
        (ok) => {
          if (selectedRunRef.current !== runId) return;
          if (!ok && !any) {
            setChatMessages((current) =>
              current.map((item) =>
                item.id === replyId
                  ? {
                      ...item,
                      text:
                        "Streaming is unavailable right now — the panels above remain the source of truth. Try the agent buttons to update evidence.",
                    }
                  : item,
              ),
            );
          }
          setChatStreaming(false);
        },
      );
    },
    [activeObject, appendChat, chatStreaming, selectedRunId],
  );

  return (
    <div className="flex h-screen flex-col bg-stone-50 text-stone-800">
      <Header claim={claim.data?.claim} status={status.data} />

      {status.isError && hasRun && (
        <div className="border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-[14px] text-amber-800">
          {status.error instanceof Error ? status.error.message : "Could not load this run."}
        </div>
      )}

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
              "flex flex-1 items-center justify-center gap-1.5 px-2 py-2 text-[14px] font-medium",
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
          {hasRun ? (
            <EvidenceRail
              runId={selectedRunId}
              activeObject={activeObject}
              agents={status.data?.agents ?? []}
            />
          ) : (
            <ShellPlaceholder />
          )}
        </aside>

        <section
          className={cn(
            "min-h-0 overflow-y-auto lg:block",
            pane === "workspace" ? "block" : "hidden",
          )}
        >
          {hasRun || pendingClaim ? (
            <div className="space-y-4 p-3 lg:p-4">
              {(claim.data?.claim?.text || pendingClaim) && (
                <InstantDraft
                  claimText={claim.data?.claim?.text || pendingClaim}
                  superseded={
                    Boolean(status.data?.completed) ||
                    (status.data?.evidence
                      ? status.data.evidence.reference_supported_steps +
                          status.data.evidence.machine_checks >
                        0
                      : false)
                  }
                />
              )}
              {hasRun ? (
                <Workspace
                  runId={selectedRunId}
                  status={status.data}
                  onChanged={refreshRun}
                  chatMessages={chatMessages}
                  chatLastNote={chatLastNote}
                  activeObject={activeObject}
                  onActiveObjectChange={setActiveObject}
                />
              ) : (
                <p className="px-1 text-[14px] text-stone-500">
                  Preparing the full read — strategies and evidence panels will
                  appear here as they are ready.
                </p>
              )}
            </div>
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
          {hasRun ? (
            <LiteratureRail
              runId={selectedRunId}
              activeObject={activeObject}
              agents={status.data?.agents ?? []}
            />
          ) : (
            <ShellPlaceholder />
          )}
        </aside>
      </main>

      <div className="flex shrink-0 items-end gap-2 border-t border-stone-200 bg-white/90 px-3 py-3">
        <SettingsButton
          runs={runs}
          selectedRunId={selectedRunId}
          onSelectRun={setSelectedRunId}
          latestRound={strategies.data?.latest_round}
        />
        <div className="min-w-0 flex-1">
          <Composer
            runId={selectedRunId}
            onRunCreated={onRunCreated}
            onSubmitClaim={setPendingClaim}
            onChatMessage={sendChat}
            chatBusy={chatStreaming}
            activeObject={activeObject}
            onClearActiveObject={() => setActiveObject(null)}
          />
        </div>
      </div>
    </div>
  );
}

function ShellPlaceholder() {
  return (
    <div className="p-4 text-[14px] text-stone-400">
      This panel fills in once a run is selected.
    </div>
  );
}

function EmptyWorkspace({ hasRuns }: { hasRuns: boolean }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-8">
      <div className="w-full max-w-2xl rounded-xl border border-stone-200 bg-white p-6">
        <h1 className="text-[18px] font-semibold text-stone-800">
          Mathematical research cockpit
        </h1>
        <p className="mt-1 text-[15px] text-stone-600">
          {hasRuns
            ? "Pick a run from the header, or state a new claim below."
            : "State a mathematical claim in the composer below to start."}
        </p>
        <ul className="mt-4 grid gap-2 text-[15px] text-stone-600 sm:grid-cols-3">
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
