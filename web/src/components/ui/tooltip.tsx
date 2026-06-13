import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/**
 * Minimal CSS-only hover/focus tooltip — no portal, no positioning lib.
 */
export function Tooltip({
  content,
  children,
  className,
  contentClassName,
}: {
  content: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  return (
    <span className={cn("group/tooltip relative inline-flex min-w-0", className)} tabIndex={0}>
      {children}
      <span
        role="tooltip"
        className={cn(
          "pointer-events-none invisible absolute left-0 top-full z-50 mt-1.5 w-max max-w-[26rem] rounded-md border border-stone-200 bg-white px-2.5 py-1.5 text-xs leading-relaxed text-stone-700 opacity-0 shadow-md transition-opacity duration-100",
          "group-hover/tooltip:visible group-hover/tooltip:opacity-100",
          "group-focus-within/tooltip:visible group-focus-within/tooltip:opacity-100",
          contentClassName,
        )}
      >
        {content}
      </span>
    </span>
  );
}
