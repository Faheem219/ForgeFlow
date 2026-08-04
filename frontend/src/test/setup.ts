import '@testing-library/jest-dom/vitest';
import { MotionGlobalConfig } from 'framer-motion';

// Make AnimatePresence exits and transitions synchronous in jsdom — tests that assert an element
// is REMOVED (closed modal, dismissed toast) must not race an exit animation's timer.
MotionGlobalConfig.skipAnimations = true;

// Node's global fetch/Request (undici) reject jsdom's AbortSignal instances with
// "Expected signal to be an instance of AbortSignal". React-router's data router passes one when
// it builds a navigation Request, which throws under jsdom. In the browser every primitive comes
// from the same realm, so this only bites in tests — strip the signal at construction here (no
// navigation needs to be aborted in a unit test).
const OriginalRequest = globalThis.Request;
class SignalTolerantRequest extends OriginalRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    if (init && 'signal' in init) {
      const rest = { ...init };
      delete rest.signal;
      super(input, rest);
    } else {
      super(input, init);
    }
  }
}
globalThis.Request = SignalTolerantRequest as unknown as typeof Request;

// jsdom implements no layout engine, so the browser APIs React Flow measures with are missing
// entirely. Without these the topology graph throws on mount instead of rendering its nodes.
if (!('ResizeObserver' in globalThis)) {
  globalThis.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
}
if (!('DOMMatrixReadOnly' in globalThis)) {
  globalThis.DOMMatrixReadOnly = class {
    m22 = 1;
  } as unknown as typeof DOMMatrixReadOnly;
}

// jsdom has no matchMedia. framer-motion's MotionConfig(reducedMotion="user") and the theme
// store's `system` mode both consult it; a guarded stub keeps them on their default branches.
// Tests that need a specific answer stub it themselves (see themeStore.test.ts).
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

// jsdom has no IntersectionObserver; framer-motion's whileInView needs one to mount.
if (typeof window !== 'undefined' && typeof window.IntersectionObserver === 'undefined') {
  class StubIntersectionObserver {
    readonly root = null;
    readonly rootMargin = '';
    readonly thresholds: readonly number[] = [];
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
      return [];
    }
  }
  (window as { IntersectionObserver: unknown }).IntersectionObserver = StubIntersectionObserver;
}
