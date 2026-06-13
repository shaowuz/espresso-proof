import { useState } from "react";
import { BookOpen } from "lucide-react";
import { useLiteraturePanel } from "../hooks/useCockpit";
import { ToneBadge } from "./ToneBadge";
import { DetailRow, ExpandableCard, PanelPlaceholder } from "./ExpandableCard";
import { PanelSection } from "./PanelSection";
import { errorText } from "../lib/api";
import { MathText } from "./MathText";

/**
 * Right rail: literature / theorem locators / source gaps (Goal 43B: its own
 * query). Every phrase and tone is server copy — a literature hit is metadata,
 * so its badge tone comes from the kernel-recorded source status, never from
 * relevance. Missing literature is shown as not-run, never as source-supported.
 */
export function LiteratureRail({ runId }: { runId: string }) {
  const lit = useLiteraturePanel(runId);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const present = lit.data && !lit.data.missing;
  const items = present ? lit.data.items : [];
  const gaps = lit.data?.source_gaps ?? [];

  return (
    <div className="flex flex-col gap-4 p-4">
      <PanelSection
        icon={BookOpen}
        title="Literature & theorems"
        count={present ? items.length : null}
        agentState={lit.data?.agent_state}
        isPending={lit.isPending}
        isError={lit.isError}
        errorText={errorText(lit.error)}
        hasData={items.length > 0}
        emptyText="No candidate references recorded yet. Run the literature agent from the workspace."
        onRetry={() => void lit.refetch()}
      >
        <div className="flex flex-col gap-1.5">
          {items.map((ref) => (
            <ExpandableCard
              key={ref.id}
              expanded={Boolean(expanded[ref.id])}
              onToggle={() =>
                setExpanded((prev) => ({ ...prev, [ref.id]: !prev[ref.id] }))
              }
              header={
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="truncate text-[13px] font-medium text-stone-800">
                    <MathText inline>{ref.title || ref.id}</MathText>
                  </span>
                  <span className="flex flex-wrap items-center gap-1">
                    {ref.locator.value !== "" && (
                      <span className="inline-flex max-w-full items-center gap-1 rounded border border-stone-200 bg-stone-50 px-1.5 py-0.5 text-[11px] text-stone-600">
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
        <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-stone-500">
          Source gaps
        </h2>
        {gaps.length === 0 ? (
          <PanelPlaceholder text="No open source gaps recorded for the current grade." />
        ) : (
          <ul className="flex flex-col gap-1">
            {gaps.map((gap, i) => (
              <li
                key={`${i}-${gap.slice(0, 24)}`}
                className="rounded-md border border-amber-200 bg-amber-50/60 px-2.5 py-1.5 text-xs text-amber-900"
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
