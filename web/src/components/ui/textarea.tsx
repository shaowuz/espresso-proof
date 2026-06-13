import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "w-full resize-none rounded-lg border border-stone-300 bg-white px-3 py-2 text-[15px] leading-relaxed text-stone-800",
      "placeholder:text-stone-400",
      "focus:border-sage-400 focus:outline-2 focus:outline-offset-0 focus:outline-sage-200",
      "disabled:cursor-not-allowed disabled:bg-stone-50 disabled:text-stone-400",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
