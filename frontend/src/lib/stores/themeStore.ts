import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * Theme state. Three modes, two resolved themes: `system` follows the OS and keeps following it,
 * which is why `mode` and `resolved` are separate fields — collapsing them would lose the user's
 * "follow my OS" intent the first time the OS flipped.
 *
 * The DOM is stamped by `applyTheme` (below) and, on first paint, by the inline script in
 * index.html. Both write the same attribute so there is no flash and no double source of truth.
 */

export type ThemeMode = 'light' | 'dark' | 'system';
export type ResolvedTheme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'forgeflow-theme';

const DARK_QUERY = '(prefers-color-scheme: dark)';

/** What the OS currently asks for. Defaults to dark when `matchMedia` is unavailable (jsdom). */
export function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return 'dark';
  return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light';
}

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  return mode === 'system' ? systemTheme() : mode;
}

/**
 * Stamp the resolved theme onto <html>. Also syncs `color-scheme` (so native form controls,
 * scrollbars and the caret follow) and the PWA `theme-color` meta (so the browser/OS chrome does).
 */
export function applyTheme(resolved: ResolvedTheme): void {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.setAttribute('data-theme', resolved);
  // `darkMode: ['class', ...]` — keep the class in step for anything keyed to it.
  root.classList.toggle('dark', resolved === 'dark');
  root.style.colorScheme = resolved;

  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', resolved === 'dark' ? '#05070d' : '#fbfbfd');
}

interface ThemeState {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
  /** Flip to the opposite of what is currently *shown* (so it works from `system` too). */
  toggle: () => void;
  /** Re-resolve after an OS change; a no-op unless the mode is `system`. */
  syncSystem: () => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      mode: 'system',
      resolved: resolveTheme('system'),
      setMode: (mode) => {
        const resolved = resolveTheme(mode);
        applyTheme(resolved);
        set({ mode, resolved });
      },
      toggle: () => {
        const next: ThemeMode = get().resolved === 'dark' ? 'light' : 'dark';
        get().setMode(next);
      },
      syncSystem: () => {
        if (get().mode !== 'system') return;
        const resolved = systemTheme();
        applyTheme(resolved);
        set({ resolved });
      },
    }),
    {
      name: THEME_STORAGE_KEY,
      // `resolved` is derived — persisting it would let a stale value win over the live OS state.
      partialize: (s) => ({ mode: s.mode }),
      onRehydrateStorage: () => (state) => {
        if (!state) return;
        const resolved = resolveTheme(state.mode);
        applyTheme(resolved);
        state.resolved = resolved;
      },
    },
  ),
);

/**
 * Start following OS theme changes. Called once from the app root; returns a teardown so tests
 * (and HMR) don't accumulate listeners.
 */
export function watchSystemTheme(): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => {};
  const media = window.matchMedia(DARK_QUERY);
  const onChange = (): void => useThemeStore.getState().syncSystem();

  // Safari < 14 only has the deprecated listener API; support both without `any`.
  if (typeof media.addEventListener === 'function') {
    media.addEventListener('change', onChange);
    return () => media.removeEventListener('change', onChange);
  }
  media.addListener(onChange);
  return () => media.removeListener(onChange);
}
