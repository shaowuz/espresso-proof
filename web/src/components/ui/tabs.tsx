import type { ButtonHTMLAttributes, HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Dumb, controlled tab primitives (no context — parent owns the state). */

export function TabsList({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex items-center gap-1 rounded-lg border border-stone-200 bg-stone-100 p-0.5",
        className,
      )}
      {...props}
    />
  );
}

export function TabsTrigger({
  active,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { active: boolean }) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={cn(
        "flex-1 rounded-md px-3 py-1 text-xs font-medium transition-colors",
        active
          ? "bg-white text-stone-800 shadow-[0_1px_2px_rgba(28,25,23,0.06)]"
          : "text-stone-500 hover:text-stone-700",
        className,
      )}
      {...props}
    />
  );
}
