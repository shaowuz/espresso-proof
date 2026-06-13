import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

type Variant = "default" | "outline" | "ghost";
type Size = "sm" | "md";

/**
 * Brand accent (sage) lives only on interactive controls — it is a
 * desaturated green deliberately distinct from the earned-green tone badge.
 */
const VARIANT_CLASSES: Record<Variant, string> = {
  default:
    "border border-transparent bg-sage-600 text-white hover:bg-sage-700 disabled:hover:bg-sage-600",
  outline:
    "border border-stone-300 bg-white text-stone-700 hover:bg-stone-50 disabled:hover:bg-white",
  ghost:
    "border border-transparent text-stone-600 hover:bg-stone-100 disabled:hover:bg-transparent",
};

const SIZE_CLASSES: Record<Size, string> = {
  sm: "h-7 gap-1 px-2.5 text-xs",
  md: "h-8 gap-1.5 px-3 text-[13px]",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "md", type = "button", ...props }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(
        "inline-flex items-center justify-center whitespace-nowrap rounded-md font-medium transition-colors",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sage-500",
        "disabled:pointer-events-none disabled:opacity-55",
        VARIANT_CLASSES[variant],
        SIZE_CLASSES[size],
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = "Button";
