import { useState, type ReactNode } from "react";
import { Calculator, FlaskConical, ShieldAlert } from "lucide-react";
import {
  useComputationsPanel,
  useCounterexamplesPanel,
  useExamplesPanel,
} from "../hooks/useCockpit";
import { ToneBadge } from "./ToneBadge";
import { DetailRow, ExpandableCard } from "./ExpandableCard";
import { PanelSection } from "./PanelSection";
import { errorText } from "../lib/api";
import { MathText } from "./MathText";
import type { ActiveObject, AgentView } from "../lib/types";

type RouteLinked = {
  related_strategy_ids?: string[];
  route_relevance?: "direct" | "pressure" | "background" | "unknown";
};

function hasRouteLinkMetadata(items: RouteLinked[]): boolean {
  return items.some((item) => Array.isArray(item.related_strategy_ids));
}

function isRelatedToActive(item: RouteLinked, activeObject: ActiveObject): boolean {
  return (
    activeObject?.kind === "strategy" &&
    Array.isArray(item.related_strategy_ids) &&
    item.related_strategy_ids.includes(activeObject.id)
  );
}

function sortRelatedFirst<T extends RouteLinked>(items: T[], activeObject: ActiveObject): T[] {
  if (activeObject?.kind !== "strategy") return items;
  return [...items].sort(
    (a, b) => Number(isRelatedToActive(b, activeObject)) - Number(isRelatedToActive(a, activeObject)),
  );
}

function RouteLinkageNote({
  activeObject,
  hasMetadata,
  label,
}: {
  activeObject: ActiveObject;
  hasMetadata: boolean;
  label: string;
}) {
  if (activeObject?.kind !== "strategy") return null;
  return (
    <p className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 text-[13px] leading-relaxed text-slate-600">
      {hasMetadata
        ? `Related ${label} appear first; other candidates remain visible.`
        : "No route linkage recorded yet; showing all evidence candidates."}
    </p>
  );
}

function RelatedBadge({
  item,
  activeObject,
}: {
  item: RouteLinked;
  activeObject: ActiveObject;
}) {
  if (!isRelatedToActive(item, activeObject)) return null;
  return (
    <span className="rounded-full border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[12px] font-medium text-indigo-700">
      active route
    </span>
  );
}

function ItemHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate text-[14px] font-medium text-stone-800"><MathText inline>{title}</MathText></span>
      {children}
    </span>
  );
}

/**
 * LEFT rail: Examples / Counterexamples / Computations — three independent
 * queries (Goal 43B). Each section loads, refreshes, and fails on its own;
 * a slow examples fetch never blocks counterexamples or computations, and
 * expanded state is keyed by item id so it survives polling.
 */
export function EvidenceRail({
  runId,
  activeObject,
  agents,
}: {
  runId: string;
  activeObject: ActiveObject;
  agents: AgentView[];
}) {
  const examples = useExamplesPanel(runId);
  const counter = useCounterexamplesPanel(runId);
  const comps = useComputationsPanel(runId);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const toggle = (id: string) =>
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  const exItems = examples.data && !examples.data.missing ? examples.data.items : [];
  const ceItems = counter.data && !counter.data.missing ? counter.data.items : [];
  const coItems = comps.data && !comps.data.missing ? comps.data.items : [];
  const exSorted = sortRelatedFirst(exItems, activeObject);
  const ceSorted = sortRelatedFirst(ceItems, activeObject);
  const coSorted = sortRelatedFirst(coItems, activeObject);
  const activeEvidenceEmptySuffix =
    activeObject?.kind === "strategy"
      ? " No route linkage recorded yet; showing all evidence candidates when available."
      : "";
  const agentByName = new Map(agents.map((agent) => [agent.agent, agent]));

  return (
    <div className="flex flex-col gap-4 p-3">
      <PanelSection
        icon={FlaskConical}
        title="Examples"
        download={{ runId, panel: "examples", label: "examples" }}
        run={{ runId, agent: "examples", onRan: () => void examples.refetch() }}
        count={examples.data && !examples.data.missing ? exItems.length : null}
        agentState={examples.data?.agent_state}
        agent={agentByName.get("examples")}
        isPending={examples.isPending}
        isRefetching={examples.isFetching}
        isError={examples.isError}
        errorText={errorText(examples.error)}
        hasData={exItems.length > 0}
        emptyText={`No examples recorded yet.${activeEvidenceEmptySuffix}`}
        onRetry={() => void examples.refetch()}
      >
        <div className="flex flex-col gap-1.5">
          <RouteLinkageNote
            activeObject={activeObject}
            hasMetadata={hasRouteLinkMetadata(exItems)}
            label="examples"
          />
          {exSorted.map((ex) => (
            <ExpandableCard
              key={ex.id}
              expanded={Boolean(expanded[ex.id])}
              onToggle={() => toggle(ex.id)}
              header={
                <ItemHeader title={ex.name || ex.id}>
                  <span className="flex flex-wrap items-center gap-1">
                    <RelatedBadge item={ex} activeObject={activeObject} />
                    <ToneBadge tone={ex.tone} phrase={ex.result_phrase} />
                    <span className="text-[13px] text-stone-500">{ex.admissibility}</span>
                  </span>
                </ItemHeader>
              }
              detail={
                <div className="flex flex-col gap-1.5">
                  {ex.behavior !== "" && <DetailRow label="Behavior"><MathText>{ex.behavior}</MathText></DetailRow>}
                  {ex.tests.length > 0 && (
                    <DetailRow label="Tests">{ex.tests.join(" · ")}</DetailRow>
                  )}
                </div>
              }
            />
          ))}
        </div>
      </PanelSection>

      <PanelSection
        icon={ShieldAlert}
        title="Counterexamples"
        download={{ runId, panel: "counterexamples", label: "counterexamples" }}
        run={{ runId, agent: "counterexamples", onRan: () => void counter.refetch() }}
        count={counter.data && !counter.data.missing ? ceItems.length : null}
        agentState={counter.data?.agent_state}
        agent={agentByName.get("counterexamples")}
        isPending={counter.isPending}
        isRefetching={counter.isFetching}
        isError={counter.isError}
        errorText={errorText(counter.error)}
        hasData={ceItems.length > 0}
        emptyText={`No counterexamples recorded.${activeEvidenceEmptySuffix}`}
        onRetry={() => void counter.refetch()}
      >
        <div className="flex flex-col gap-1.5">
          <RouteLinkageNote
            activeObject={activeObject}
            hasMetadata={hasRouteLinkMetadata(ceItems)}
            label="counterexamples"
          />
          {ceSorted.map((ce) => (
            <ExpandableCard
              key={ce.id}
              expanded={Boolean(expanded[ce.id])}
              onToggle={() => toggle(ce.id)}
              header={
                <ItemHeader title={ce.example || ce.id}>
                  <span className="flex flex-wrap items-center gap-1">
                    <RelatedBadge item={ce} activeObject={activeObject} />
                    <ToneBadge tone={ce.tone} phrase={ce.phrase} />
                  </span>
                </ItemHeader>
              }
              detail={
                <div className="flex flex-col gap-1.5">
                  {ce.explanation !== "" && (
                    <DetailRow label="Explanation"><MathText>{ce.explanation}</MathText></DetailRow>
                  )}
                  <DetailRow label="Admissibility">{ce.admissibility}</DetailRow>
                  {ce.repair !== "" && (
                    <DetailRow label="Repaired claim"><MathText>{ce.repair}</MathText></DetailRow>
                  )}
                </div>
              }
            />
          ))}
        </div>
      </PanelSection>

      <PanelSection
        icon={Calculator}
        title="Computations"
        download={{ runId, panel: "computations", label: "computations" }}
        count={comps.data && !comps.data.missing ? coItems.length : null}
        agentState={comps.data?.agent_state}
        agent={agentByName.get("computations")}
        isPending={comps.isPending}
        isRefetching={comps.isFetching}
        isError={comps.isError}
        errorText={errorText(comps.error)}
        hasData={coItems.length > 0}
        emptyText={`No computation checks recorded yet.${activeEvidenceEmptySuffix}`}
        onRetry={() => void comps.refetch()}
      >
        <div className="flex flex-col gap-1.5">
          <RouteLinkageNote
            activeObject={activeObject}
            hasMetadata={hasRouteLinkMetadata(coItems)}
            label="computations"
          />
          {coSorted.map((co) => (
            <ExpandableCard
              key={co.id}
              expanded={Boolean(expanded[co.id])}
              onToggle={() => toggle(co.id)}
              header={
                <ItemHeader title={co.quantity || co.id}>
                  <span className="flex flex-wrap items-center gap-1">
                    <RelatedBadge item={co} activeObject={activeObject} />
                    <ToneBadge tone={co.tone} phrase={co.phrase} />
                    <span className="text-[13px] text-stone-500">{co.method}</span>
                  </span>
                </ItemHeader>
              }
              detail={<DetailRow label="Result"><MathText>{co.result}</MathText></DetailRow>}
            />
          ))}
        </div>
      </PanelSection>
    </div>
  );
}
