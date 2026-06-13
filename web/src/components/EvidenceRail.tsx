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

function ItemHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <span className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate text-xs font-medium text-stone-800"><MathText inline>{title}</MathText></span>
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
export function EvidenceRail({ runId }: { runId: string }) {
  const examples = useExamplesPanel(runId);
  const counter = useCounterexamplesPanel(runId);
  const comps = useComputationsPanel(runId);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const toggle = (id: string) =>
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  const exItems = examples.data && !examples.data.missing ? examples.data.items : [];
  const ceItems = counter.data && !counter.data.missing ? counter.data.items : [];
  const coItems = comps.data && !comps.data.missing ? comps.data.items : [];

  return (
    <div className="flex flex-col gap-4 p-3">
      <PanelSection
        icon={FlaskConical}
        title="Examples"
        count={examples.data && !examples.data.missing ? exItems.length : null}
        agentState={examples.data?.agent_state}
        isPending={examples.isPending}
        isError={examples.isError}
        errorText={errorText(examples.error)}
        hasData={exItems.length > 0}
        emptyText="No examples recorded yet."
        onRetry={() => void examples.refetch()}
      >
        <div className="flex flex-col gap-1.5">
          {exItems.map((ex) => (
            <ExpandableCard
              key={ex.id}
              expanded={Boolean(expanded[ex.id])}
              onToggle={() => toggle(ex.id)}
              header={
                <ItemHeader title={ex.name || ex.id}>
                  <span className="flex flex-wrap items-center gap-1">
                    <ToneBadge tone={ex.tone} phrase={ex.result_phrase} />
                    <span className="text-[11px] text-stone-500">{ex.admissibility}</span>
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
        count={counter.data && !counter.data.missing ? ceItems.length : null}
        agentState={counter.data?.agent_state}
        isPending={counter.isPending}
        isError={counter.isError}
        errorText={errorText(counter.error)}
        hasData={ceItems.length > 0}
        emptyText="No counterexamples recorded. This is not a proof of safety — needs check."
        onRetry={() => void counter.refetch()}
      >
        <div className="flex flex-col gap-1.5">
          {ceItems.map((ce) => (
            <ExpandableCard
              key={ce.id}
              expanded={Boolean(expanded[ce.id])}
              onToggle={() => toggle(ce.id)}
              header={
                <ItemHeader title={ce.example || ce.id}>
                  <ToneBadge tone={ce.tone} phrase={ce.phrase} />
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
        count={comps.data && !comps.data.missing ? coItems.length : null}
        agentState={comps.data?.agent_state}
        isPending={comps.isPending}
        isError={comps.isError}
        errorText={errorText(comps.error)}
        hasData={coItems.length > 0}
        emptyText="No computation checks recorded yet."
        onRetry={() => void comps.refetch()}
      >
        <div className="flex flex-col gap-1.5">
          {coItems.map((co) => (
            <ExpandableCard
              key={co.id}
              expanded={Boolean(expanded[co.id])}
              onToggle={() => toggle(co.id)}
              header={
                <ItemHeader title={co.quantity || co.id}>
                  <span className="flex flex-wrap items-center gap-1">
                    <ToneBadge tone={co.tone} phrase={co.phrase} />
                    <span className="text-[11px] text-stone-500">{co.method}</span>
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
