/**
 * Motion helpers.
 *
 * There is no animation library here on purpose (IMPLEMENTATION_PLAN.md §4 fixes the frontend
 * stack). Everything declarative lives in CSS — `src/app/theme/index.css` + the Tailwind keyframes.
 * This module covers only the two things CSS cannot express: asking whether motion is wanted, and
 * driving the native View Transition API.
 */

/** Whether the user has asked the OS to reduce motion. Safe under jsdom (no `matchMedia`). */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

interface ViewTransition {
  finished: Promise<void>;
}

type DocumentWithViewTransition = Document & {
  startViewTransition?: (callback: () => void | Promise<void>) => ViewTransition;
};

/**
 * Run `update` inside a native view transition when the browser supports one and the user has not
 * opted out; otherwise just run it. Callers must not depend on the transition happening — this is
 * a progressive enhancement, which is why the fallback is a plain call rather than a polyfill.
 */
export function withViewTransition(update: () => void): void {
  if (typeof document === 'undefined') {
    update();
    return;
  }
  const doc = document as DocumentWithViewTransition;
  if (typeof doc.startViewTransition !== 'function' || prefersReducedMotion()) {
    update();
    return;
  }
  doc.startViewTransition(update);
}

/**
 * Stagger delay for list entrance animations, capped so a long list never leaves its tail visibly
 * loading. Returns a style object rather than a class because the delay is per-index.
 */
export function stagger(index: number, stepMs = 24, maxMs = 240): { animationDelay: string } {
  return { animationDelay: `${Math.min(index * stepMs, maxMs)}ms` };
}
