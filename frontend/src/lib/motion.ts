import type { Transition, Variants } from 'framer-motion';

/**
 * Shared motion vocabulary. One easing, three speeds, a handful of variants — so every surface
 * moves the same way and nothing invents its own timing. Fast and quiet by design: motion here
 * confirms an action or establishes hierarchy, it never performs.
 */

/** Ease-out-expo-ish: fast start, soft landing. The house curve. */
export const EASE: Transition['ease'] = [0.16, 1, 0.3, 1];

export const DURATION = {
  /** Hovers, small fades. */
  fast: 0.12,
  /** Modals, dropdowns, list items. */
  base: 0.18,
  /** Page-level transitions. */
  slow: 0.28,
} as const;

export const fadeIn: Variants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { duration: DURATION.base, ease: EASE } },
};

export const riseIn: Variants = {
  hidden: { opacity: 0, y: 10 },
  visible: { opacity: 1, y: 0, transition: { duration: DURATION.base, ease: EASE } },
};

/** Parent for staggered lists: children with `riseIn` cascade in. */
export const staggerChildren = (delay = 0.04): Variants => ({
  hidden: {},
  visible: { transition: { staggerChildren: delay } },
});

/** Route-level crossfade — short enough that navigation never feels gated. */
export const pageTransition = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -4 },
  transition: { duration: 0.15, ease: EASE },
} as const;
