import type { ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Card } from "./ui/card";
import { Collapse } from "./motion";
import { cn } from "../lib/cn";

/**
 * Compact evidence/literature item card with an expandable detail section.
 * Expansion is controlled by the parent (keyed by item id) so it survives
 * the 4s snapshot refreshes.
 */
export function ExpandableCard({
  expanded,
  onToggle,
  header,
  detail,
  footer,
  className,
}: {
  expanded: boolean;
  onToggle: () => void;
  header: ReactNode;
  detail: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  const Chevron = expanded ? ChevronDown : ChevronRight;
  return (
    <Card className={cn("overflow-hidden", className)}>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-start gap-1.5 px-2.5 py-2 text-left hover:bg-stone-50"
      >
        <Chevron className="mt-0.5 h-3.5 w-3.5 shrink-0 text-stone-400" aria-hidden />
        <span className="min-w-0 flex-1">{header}</span>
      </button>
      <Collapse open={expanded}>
        <div className="border-t border-stone-100 px-2.5 py-2 pl-7">{detail}</div>
      </Collapse>
      {footer !== undefined && (
        <div className="border-t border-stone-100 px-2.5 py-1.5 pl-7">{footer}</div>
      )}
    </Card>
  );
}

/** A small labeled field inside an expanded detail section. */
export function DetailRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mb-1.5 last:mb-0">
      <div className="text-[10.5px] font-semibold uppercase tracking-wide text-stone-400">
        {label}
      </div>
      <div className="text-xs leading-relaxed text-stone-700">{children}</div>
    </div>
  );
}

/** Explicit placeholder — missing panels are never blank, never implied success. */
export function PanelPlaceholder({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-dashed border-stone-300 bg-stone-50/60 px-3 py-3 text-xs text-stone-500">
      {text}
    </div>
  );
}
