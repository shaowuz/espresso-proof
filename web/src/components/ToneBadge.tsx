import { Badge } from "./ui/badge";
import { toneBadgeClass } from "../lib/tones";
import { cn } from "../lib/cn";

/**
 * The ONLY status badge in the app. Color comes exclusively from the
 * server-computed tone via the tones module; the phrase is server copy.
 */
export function ToneBadge({
  tone,
  phrase,
  className,
}: {
  tone: unknown;
  phrase: string;
  className?: string;
}) {
  if (phrase.trim() === "") return null;
  return (
    <Badge className={cn(toneBadgeClass(tone), className)}>
      <span className="truncate">{phrase}</span>
    </Badge>
  );
}
