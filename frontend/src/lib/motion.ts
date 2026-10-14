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

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/**
 * Gate for every entrance animation. False under vitest (jsdom has no layout, and tests must see
 * final states, not mid-tween ones) and for users who ask the OS for less motion — content then
 * simply renders in place, which is the correct reduced experience.
 *
 * Every animated surface asks this first; with it false, content renders in its final state.
 */
export function motionOK(): boolean {
  return (
    typeof window !== 'undefined' && import.meta.env.MODE !== 'test' && !prefersReducedMotion()
  );
}

/**
 * A one-shot opacity fade via the Web Animations API — no library, nothing left behind on the
 * element afterwards. Opacity only: a transform on an ancestor would re-anchor every
 * `position: fixed` descendant (the workspace's focus overlay) to that ancestor.
 */
export function fadeInElement(element: HTMLElement | null, duration = 180): void {
  if (!element || !motionOK() || typeof element.animate !== 'function') return;
  element.animate([{ opacity: 0 }, { opacity: 1 }], {
    duration,
    easing: 'cubic-bezier(0.16, 1, 0.3, 1)',
  });
}
