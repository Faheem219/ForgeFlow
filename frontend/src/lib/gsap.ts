import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { Draggable } from 'gsap/Draggable';
import { Flip } from 'gsap/Flip';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { SplitText } from 'gsap/SplitText';

/**
 * Central GSAP setup — the only module that registers plugins, so no component ever has to.
 *
 * Division of labor with `lib/motion.ts` (framer-motion): GSAP owns *choreography* — timelines,
 * scroll-triggered reveals, staggered entrances, counters, the landing page. framer-motion keeps
 * the React presence work it is already load-bearing for (Modal/Toaster enter-exit, the Tabs
 * `layoutId` underline, ThemeToggle icon swap). Both share the same easing family.
 */

// Every plugin is free as of GSAP 3.13 (the Webflow acquisition), so Flip is available without a
// membership or auth token — it drives the dashboard's reflow when a project is removed.
gsap.registerPlugin(useGSAP, Draggable, Flip, ScrollTrigger, SplitText);

/**
 * The app shell's scroll container. ScrollTrigger defaults to the window, but every signed-in page
 * scrolls inside `main` — so anything scroll-driven in the app (not the landing, which owns the
 * document scroll) must point at this selector.
 */
export const APP_SCROLLER = '[data-app-scroll]';

/**
 * Run animation setup so that a failure inside it can never take the page down with it.
 *
 * Earned the hard way: a decorative `ScrollTrigger.batch` threw during init and React Router
 * replaced the entire dashboard with an error screen. Motion is the one layer of this app that is
 * *always* optional — every surface is required to be complete and usable without it — so a broken
 * tween must degrade to "no animation", never to "no page".
 */
export function safeAnimate(label: string, setup: () => void | (() => void)): void | (() => void) {
  try {
    // The cleanup MUST be forwarded. Swallowing it here silently leaked every ScrollTrigger and
    // listener the callback created, so each hot reload stacked another copy on the same elements
    // and they fought over the same transforms — which looks exactly like "the animation broke".
    return setup();
  } catch (error) {
    console.warn(`[motion] ${label} failed; continuing without it.`, error);
    return undefined;
  }
}

/** GSAP twin of the house curve in `lib/motion.ts` ([0.16, 1, 0.3, 1] ≈ expo.out). */
export const EASE_OUT = 'expo.out';

/** Entrance-scale durations (seconds). Micro-interaction timing stays in `lib/motion.ts`. */
export const GD = {
  fast: 0.35,
  base: 0.6,
  slow: 0.9,
} as const;

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/**
 * Gate for every entrance/scroll animation. False under vitest (jsdom has no layout, and tests
 * must see final states, not mid-tween ones) and for users who ask the OS for less motion —
 * content then simply renders in place, which is the correct reduced experience.
 */
export function motionOK(): boolean {
  return (
    typeof window !== 'undefined' && import.meta.env.MODE !== 'test' && !prefersReducedMotion()
  );
}

/**
 * A hover/press state that comes back gracefully, using GSAP's `easeReverse`.
 *
 * Expressive eases are the reason UI motion often feels *wrong on the way out*: `back` overshoots
 * and `elastic` wobbles, which reads as personality entering and as indecision leaving. `easeReverse`
 * is a tween-level vars property (GSAP resolves it as `vars.easeReverse || vars.yoyoEase`) that lets
 * the same tween use a different curve when reversed — so the element can arrive with character and
 * leave cleanly. The reverse also runs slightly faster via `timeScale`, because an exit that takes
 * as long as the entrance feels reluctant.
 *
 * Returns a teardown that kills the tween and detaches the listeners.
 */
export function reversibleHover(
  element: HTMLElement,
  to: gsap.TweenVars,
  options: { ease?: string; easeReverse?: string; duration?: number; exitSpeed?: number } = {},
): () => void {
  const {
    ease = 'back.out(2.2)',
    easeReverse = 'power2.out',
    duration = 0.4,
    exitSpeed = 1.35,
  } = options;

  const tween = gsap.to(element, { ...to, duration, ease, easeReverse, paused: true });
  const enter = (): void => {
    tween.timeScale(1).play();
  };
  const leave = (): void => {
    tween.timeScale(exitSpeed).reverse();
  };

  element.addEventListener('pointerenter', enter);
  element.addEventListener('pointerleave', leave);
  element.addEventListener('focus', enter);
  element.addEventListener('blur', leave);

  return () => {
    element.removeEventListener('pointerenter', enter);
    element.removeEventListener('pointerleave', leave);
    element.removeEventListener('focus', enter);
    element.removeEventListener('blur', leave);
    tween.kill();
  };
}

export { Draggable, Flip, gsap, ScrollTrigger, SplitText, useGSAP };
