import { useState } from "react";
import { BookOpen } from "lucide-react";
import type { Snapshot } from "../lib/types";
import { ToneBadge } from "./ToneBadge";
import { DetailRow, ExpandableCard, PanelPlaceholder } from "./ExpandableCard";

/**
 * Right rail: literature / theorem locators / source gaps.
 *
 * Every phrase and tone is server copy. A literature hit is metadata: its
 * badge tone comes from the kernel-recorded source status, never from mere
 * relevance — this component cannot mint trust.
 */
export function LiteratureRail({ snapshot }: { snapshot: Snapshot }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const { literature, source_gaps, missing } = snapshot;

  return (
    <div className="flex flex-col gap-4 p-4">
      <section>
        <h2 className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-stone-500">
          <BookOpen className="h-3.5 w-3.5" aria-hidden /> Literature & theorems
        </h2>
        {missing.literature ? (
          <PanelPlaceholder text="Literature review has not run yet. Run the literature agent from the workspace." />
        ) : literature.length === 0 ? (
          <PanelPlaceholder text="No candidate references recorded yet." />
        ) : (
          <div className="flex flex-col gap-1.5">
            {literature.map((ref) => (
              <ExpandableCard
                key={ref.id}
                expanded={Boolean(expanded[ref.id])}
                onToggle={() =>
                  setExpanded((prev) => ({ ...prev, [ref.id]: !prev[ref.id] }))
                }
                header={
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="truncate text-[13px] font-medium text-stone-800">
                      {ref.title || ref.id}
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
                      <DetailRow label="Relevance">{ref.relevance}</DetailRow>
                    )}
                    {ref.why !== "" && (
                      <DetailRow label="Why relevant">{ref.why}</DetailRow>
                    )}
                  </div>
                }
              />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-stone-500">
          Source gaps
        </h2>
        {source_gaps.length === 0 ? (
          <PanelPlaceholder
            text={
              missing.grade
                ? "Not assessed yet."
                : "No open source gaps recorded for the current grade."
            }
          />
        ) : (
          <ul className="flex flex-col gap-1">
            {source_gaps.map((gap, i) => (
              <li
                key={`${i}-${gap.slice(0, 24)}`}
                className="rounded-md border border-amber-200 bg-amber-50/60 px-2.5 py-1.5 text-xs text-amber-900"
              >
                {gap}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
