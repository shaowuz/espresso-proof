import { useRef, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

/**
 * Goal 43E: motion primitives. Motion is interaction polish ONLY — it never
 * changes artifacts, status, or tone, and it never hides missing evidence. When
 * the user prefers reduced motion, expansions become instant and fades are
 * dropped (the content still appears — motion can never gate information).
 *
 * Layout stability: Collapse animates height/opacity, not position, so columns
 * never jump and the selected route never reflows out of view.
 */

/** Animated expand/collapse of a detail region. */
export function Collapse({ open, children }: { open: boolean; children: ReactNode }) {
  const reduce = useReducedMotion();
  return (
    <AnimatePresence initial={false}>
      {open && (
        <motion.div
          key="content"
          initial={reduce ? { opacity: 1 } : { height: 0, opacity: 0 }}
          animate={reduce ? { opacity: 1 } : { height: "auto", opacity: 1 }}
          exit={reduce ? { opacity: 1 } : { height: 0, opacity: 0 }}
          transition={{ duration: reduce ? 0 : 0.18, ease: "easeOut" }}
          style={{ overflow: "hidden" }}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

/**
 * Fade a block in when it first appears (loading skeleton → content, a new
 * panel item, a fresh ledger). A no-op under reduced motion. `motionKey` lets
 * the caller re-trigger the fade when the underlying content identity changes.
 */
export function FadeIn({
  children,
  motionKey,
  className,
}: {
  children: ReactNode;
  motionKey?: string | number;
  className?: string;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;
  return (
    <motion.div
      key={motionKey}
      className={className}
      initial={{ opacity: 0, y: 2 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.16, ease: "easeOut" }}
    >
      {children}
    </motion.div>
  );
}

/**
 * Stable-id entrance animation. A key animates only the first time this list
 * observes it, so background polling with the same ids does not replay motion.
 */
export function AnimatedList<T>({
  items,
  getKey,
  children,
  className,
}: {
  items: T[];
  getKey: (item: T) => string;
  children: (item: T) => ReactNode;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const seen = useRef<Set<string>>(new Set());

  return (
    <div className={className}>
      {items.map((item, index) => {
        const key = getKey(item);
        const known = seen.current.has(key);
        if (!known) seen.current.add(key);
        if (reduce || known) {
          return <div key={key}>{children(item)}</div>;
        }
        return (
          <motion.div
            key={key}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{
              duration: 0.18,
              delay: Math.min(index, 5) * 0.035,
              ease: "easeOut",
            }}
          >
            {children(item)}
          </motion.div>
        );
      })}
    </div>
  );
}

/** A soft pulsing dot for active (running/queued) process states. Never green. */
export function PulseDot({ className }: { className?: string }) {
  const reduce = useReducedMotion();
  if (reduce) return <span className={className} />;
  return (
    <motion.span
      className={className}
      animate={{ opacity: [1, 0.4, 1] }}
      transition={{ duration: 1.1, repeat: Infinity, ease: "easeInOut" }}
    />
  );
}
