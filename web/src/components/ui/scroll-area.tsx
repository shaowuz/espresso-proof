import { forwardRef, type HTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Simple native scroll container with the min-h-0 plumbing baked in. */
export const ScrollArea = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("min-h-0 flex-1 overflow-y-auto overscroll-contain", className)}
      {...props}
    />
  ),
);
ScrollArea.displayName = "ScrollArea";
