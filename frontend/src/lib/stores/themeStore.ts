import { create } from 'zustand';

import { META_THEME_COLOR, type ResolvedTheme } from '../../app/theme/palette';

export type ThemeMode = ResolvedTheme | 'system';

/**
 * Deliberately a RAW string in localStorage — not zustand `persist`.
 *
 * The pre-paint script in index.html must read this key before any bundle loads to stamp the
 * theme without a flash. A raw `'light' | 'dark' | 'system'` string is a stable contract that
 * script can `localStorage.getItem` directly; a persist envelope would couple the inline script
 * to zustand's internal JSON shape and silently break on a version bump.
 */
export const THEME_STORAGE_KEY = 'forgeflow-theme-mode';

interface ThemeState {
  mode: ThemeMode;
  /** What is actually on screen — `system` resolved against the OS preference. */
  resolved: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
  /** Flip relative to what is SHOWN (not the stored mode), so it works from `system` too. */
  toggle: () => void;
  /** Re-resolve `system` mode against the current OS preference. No-op for explicit modes. */
  syncSystem: () => void;
}

function systemPrefersLight(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: light)').matches
  );
}

export function resolveMode(mode: ThemeMode): ResolvedTheme {
  if (mode === 'system') return systemPrefersLight() ? 'light' : 'dark';
  return mode;
}

function readStoredMode(): ThemeMode {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'dark';
  } catch {
    return 'dark';
  }
}

/**
 * Stamp a resolved theme onto the document: `data-theme` drives the token variables, `.dark`
 * keeps third-party class-keyed CSS in step, `color-scheme` fixes form controls + scrollbars,
 * and the meta swap keeps the browser chrome matching the canvas.
 */
export function applyTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement;
  root.dataset.theme = resolved;
  root.classList.toggle('dark', resolved === 'dark');
  root.style.colorScheme = resolved;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', META_THEME_COLOR[resolved]);
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  mode: readStoredMode(),
  resolved: resolveMode(readStoredMode()),
  setMode: (mode) => {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, mode);
    } catch {
      // Storage unavailable (private mode) — the theme still applies for this session.
    }
    const resolved = resolveMode(mode);
    applyTheme(resolved);
    set({ mode, resolved });
  },
  toggle: () => {
    get().setMode(get().resolved === 'dark' ? 'light' : 'dark');
  },
  syncSystem: () => {
    if (get().mode !== 'system') return;
    const resolved = resolveMode('system');
    applyTheme(resolved);
    set({ resolved });
  },
}));

/**
 * Apply the stored theme and follow OS changes while in `system` mode.
 * Mounted once from providers; returns an unsubscribe for tests.
 */
export function watchSystemTheme(): () => void {
  applyTheme(useThemeStore.getState().resolved);
  if (typeof window.matchMedia !== 'function') return () => undefined;

  const query = window.matchMedia('(prefers-color-scheme: light)');
  const onChange = (): void => useThemeStore.getState().syncSystem();

  // Safari <14 only has the deprecated pair; feature-detect rather than assume.
  if (typeof query.addEventListener === 'function') {
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }
  query.addListener(onChange);
  return () => query.removeListener(onChange);
}
