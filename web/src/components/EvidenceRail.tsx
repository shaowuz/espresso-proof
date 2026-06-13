import { useState, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Calculator, FlaskConical, ShieldAlert } from "lucide-react";
import type { Snapshot } from "../lib/types";
import { ToneBadge } from "./ToneBadge";
import { DetailRow, ExpandableCard, PanelPlaceholder } from "./ExpandableCard";

function SectionHeader({
  icon: Icon,
  title,
  count,
}: {
  icon: LucideIcon;
  title: string;
  count: number | null;
}) {
  return (
    <div className="mb-1.5 flex items-center gap-1.5">
      <Icon className="h-3.5 w-3.5 text-stone-400" aria-hidden />
      <h2 className="text-xs font-semibold uppercase tracking-wide text-stone-500">
        {title}
      </h2>
      {count !== null && <span className="text-[11px] text-stone-400">{count}</span>}
    </div>
  );
}

function ItemHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate text-xs font-medium text-stone-800">{title}</span>
      {children}
    </span>
  );
}

/**
 * LEFT rail: Examples / Counterexamples / Computations.
 * Expanded state is keyed by item id so it survives snapshot polling.
 */
export function EvidenceRail({ snapshot }: { snapshot: Snapshot }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const toggle = (id: string) =>
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  const { examples, counterexamples, computations, missing } = snapshot;

  return (
    <div className="flex flex-col gap-4 p-3">
      <section>
        <SectionHeader
          icon={FlaskConical}
          title="Examples"
          count={missing.examples ? null : examples.length}
        />
        {missing.examples ? (
          <PanelPlaceholder text="Not run yet — no examples have been tried." />
        ) : examples.length === 0 ? (
          <PanelPlaceholder text="The example pass finished without recording examples." />
        ) : (
          <div className="flex flex-col gap-1.5">
            {examples.map((ex) => (
              <ExpandableCard
                key={ex.id}
                expanded={expanded[ex.id] === true}
                onToggle={() => toggle(ex.id)}
                header={
                  <ItemHeader title={ex.name !== "" ? ex.name : ex.id}>
                    <ToneBadge tone={ex.tone} phrase={ex.result_phrase} />
                  </ItemHeader>
                }
                detail={
                  <>
                    {ex.behavior !== "" && (
                      <DetailRow label="Behavior">{ex.behavior}</DetailRow>
                    )}
                    {ex.admissibility !== "" && (
                      <DetailRow label="Admissibility">{ex.admissibility}</DetailRow>
                    )}
                    {ex.tests.length > 0 && (
                      <DetailRow label="Tests">
                        <ul className="list-disc space-y-0.5 pl-4">
                          {ex.tests.map((t, i) => (
                            <li key={i}>{t}</li>
                          ))}
                        </ul>
                      </DetailRow>
                    )}
                  </>
                }
              />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionHeader
          icon={ShieldAlert}
          title="Counterexamples"
          count={missing.counterexamples ? null : counterexamples.length}
        />
        {missing.counterexamples ? (
          <PanelPlaceholder text="Not run yet — no counterexample search so far." />
        ) : counterexamples.length === 0 ? (
          <PanelPlaceholder text="No counterexamples recorded. This is not a proof of safety — needs check." />
        ) : (
          <div className="flex flex-col gap-1.5">
            {counterexamples.map((cx) => (
              <ExpandableCard
                key={cx.id}
                expanded={expanded[cx.id] === true}
                onToggle={() => toggle(cx.id)}
                header={
                  <ItemHeader title={cx.example !== "" ? cx.example : cx.id}>
                    <ToneBadge tone={cx.tone} phrase={cx.phrase} />
                  </ItemHeader>
                }
                detail={
                  <>
                    {cx.explanation !== "" && (
                      <DetailRow label="Explanation">{cx.explanation}</DetailRow>
                    )}
                    {cx.admissibility !== "" && (
                      <DetailRow label="Admissibility">{cx.admissibility}</DetailRow>
                    )}
                    {cx.repair !== "" && (
                      <DetailRow label="Suggested repair">{cx.repair}</DetailRow>
                    )}
                  </>
                }
              />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionHeader
          icon={Calculator}
          title="Computations"
          count={missing.computations ? null : computations.length}
        />
        {missing.computations ? (
          <PanelPlaceholder text="Not run yet — no computations have been attempted." />
        ) : computations.length === 0 ? (
          <PanelPlaceholder text="The computation pass finished without recording results." />
        ) : (
          <div className="flex flex-col gap-1.5">
            {computations.map((c) => (
              <ExpandableCard
                key={c.id}
                expanded={expanded[c.id] === true}
                onToggle={() => toggle(c.id)}
                header={
                  <ItemHeader title={c.quantity !== "" ? c.quantity : c.id}>
                    {c.result !== "" && (
                      <span className="truncate font-mono text-[11px] text-stone-600">
                        {c.result}
                      </span>
                    )}
                    <ToneBadge tone={c.tone} phrase={c.phrase} />
                  </ItemHeader>
                }
                detail={
                  <>
                    {c.method !== "" && <DetailRow label="Method">{c.method}</DetailRow>}
                    {c.result !== "" && (
                      <DetailRow label="Result">
                        <span className="font-mono">{c.result}</span>
                      </DetailRow>
                    )}
                  </>
                }
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
