import { useState } from "react";
import { BookOpen } from "lucide-react";
import { useLiteraturePanel } from "../hooks/useCockpit";
import { ToneBadge } from "./ToneBadge";
import { DetailRow, ExpandableCard, PanelPlaceholder } from "./ExpandableCard";
import { PanelSection } from "./PanelSection";
import { errorText } from "../lib/api";
import { MathText } from "./MathText";
import type { ActiveObject, AgentView, LiteratureView } from "../lib/types";

function hasRouteLinkMetadata(items: LiteratureView[]): boolean {
  return items.some((item) => Array.isArray(item.related_strategy_ids));
}

function isRelatedToActive(item: LiteratureView, activeObject: ActiveObject): boolean {
  return (
    activeObject?.kind === "strategy" &&
    Array.isArray(item.related_strategy_ids) &&
    item.related_strategy_ids.includes(activeObject.id)
  );
}

function sortRelatedFirst(items: LiteratureView[], activeObject: ActiveObject): LiteratureView[] {
  if (activeObject?.kind !== "strategy") return items;
  return [...items].sort(
    (a, b) => Number(isRelatedToActive(b, activeObject)) - Number(isRelatedToActive(a, activeObject)),
  );
}

function RouteLiteratureNote({
  activeObject,
  hasMetadata,
}: {
  activeObject: ActiveObject;
  hasMetadata: boolean;
}) {
  if (activeObject?.kind !== "strategy") return null;
  return (
    <p className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1.5 text-[13px] leading-relaxed text-slate-600">
      {hasMetadata
        ? "Related literature appears first; source status still comes only from server evidence."
        : "Source linkage is not route-specific yet; showing all literature candidates."}
    </p>
  );
}

/**
 * Right rail: literature / theorem locators / source gaps (its own
 * query). Every phrase and tone is server copy — a literature hit is metadata,
 * so its badge tone comes from the kernel-recorded source status, never from
 * relevance. Missing literature is shown as not-run, never as source-supported.
 */
export function LiteratureRail({
  runId,
  activeObject,
  agents,
}: {
  runId: string;
  activeObject: ActiveObject;
  agents: AgentView[];
}) {
  const lit = useLiteraturePanel(runId);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const present = lit.data && !lit.data.missing;
  const items = present ? lit.data.items : [];
  const gaps = lit.data?.source_gaps ?? [];
  const sortedItems = sortRelatedFirst(items, activeObject);
  const activeLiteratureEmptySuffix =
    activeObject?.kind === "strategy"
      ? " Source linkage is not route-specific yet; showing all literature candidates when available."
      : "";
  const literatureAgent = agents.find((agent) => agent.agent === "literature");
  const emptyText = (() => {
    if (lit.data?.agent_state === "running" || lit.data?.agent_state === "queued") {
      return `Literature is ${lit.data.agent_state}; candidate references will appear here.`;
    }
    if (lit.data?.agent_state === "not_run" || lit.data?.missing) {
      return `Not run yet — no candidate references recorded. Run the literature agent from this panel.${activeLiteratureEmptySuffix}`;
    }
    return `No candidate references found.${activeLiteratureEmptySuffix}`;
  })();

  return (
    <div className="flex flex-col gap-4 p-4">
      <PanelSection
        icon={BookOpen}
        title="Literature & theorems"
        download={{ runId, panel: "literature", label: "literature" }}
        run={{ runId, agent: "literature", onRan: () => void lit.refetch() }}
        count={present ? items.length : null}
        agentState={lit.data?.agent_state}
        agent={literatureAgent}
        pressureLane={lit.data?.pressure_lane}
        isPending={lit.isPending}
        isRefetching={lit.isFetching}
        isError={lit.isError}
        errorText={errorText(lit.error)}
        hasData={items.length > 0}
        emptyText={emptyText}
        onRetry={() => void lit.refetch()}
      >
        <div className="flex flex-col gap-1.5">
          <RouteLiteratureNote
            activeObject={activeObject}
            hasMetadata={hasRouteLinkMetadata(items)}
          />
          {sortedItems.map((ref) => (
            <ExpandableCard
              key={ref.id}
              expanded={Boolean(expanded[ref.id])}
              onToggle={() =>
                setExpanded((prev) => ({ ...prev, [ref.id]: !prev[ref.id] }))
              }
              header={
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="truncate text-[15px] font-medium text-stone-800">
                    <MathText inline>{ref.title || ref.id}</MathText>
                  </span>
                  <span className="flex flex-wrap items-center gap-1">
                    {isRelatedToActive(ref, activeObject) && (
                      <span className="rounded-full border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[12px] font-medium text-indigo-700">
                        active route
                      </span>
                    )}
                    {ref.locator.value !== "" && (
                      <span className="inline-flex max-w-full items-center gap-1 rounded border border-stone-200 bg-stone-50 px-1.5 py-0.5 text-[13px] text-stone-600">
                        <span className="font-medium uppercase tracking-wide text-stone-400">
                          {ref.locator.kind || "ref"}
                        </span>
                        <span className="truncate">{ref.locator.value}</span>
                      </span>
                    )}
                    <ToneBadge tone={ref.tone} phrase={ref.phrase} />
                  </span>
                </span>
              }
              detail={
                <div className="flex flex-col gap-1.5">
                  {ref.authors.length > 0 && (
                    <DetailRow label="Authors">{ref.authors.join(", ")}</DetailRow>
                  )}
                  {ref.relevance !== "" && (
                    <DetailRow label="Relevance"><MathText>{ref.relevance}</MathText></DetailRow>
                  )}
                  {ref.why !== "" && <DetailRow label="Why relevant"><MathText>{ref.why}</MathText></DetailRow>}
                </div>
              }
            />
          ))}
        </div>
      </PanelSection>

      <section>
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <h2 className="text-[13px] font-semibold uppercase tracking-wide text-stone-500">
            Source gaps
          </h2>
          {activeObject?.kind === "strategy" && (
            <span className="rounded-full border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[12px] text-slate-500">
              active route context does not create support
            </span>
          )}
        </div>
        {gaps.length === 0 ? (
          <PanelPlaceholder text="No open source gaps recorded for the current grade." />
        ) : (
          <ul className="flex flex-col gap-1">
            {gaps.map((gap, i) => (
              <li
                key={`${i}-${gap.slice(0, 24)}`}
                className="rounded-md border border-amber-200 bg-amber-50/60 px-2.5 py-1.5 text-[14px] text-amber-900"
              >
                <MathText>{gap}</MathText>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
