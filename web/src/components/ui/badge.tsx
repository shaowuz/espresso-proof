import type { HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-full border border-stone-200 bg-stone-100 px-2 py-0.5 text-[11px] font-medium leading-4 text-stone-600",
        className,
      )}
      {...props}
    />
  );
}
