import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { THEME_STORAGE_KEY, applyTheme, resolveTheme, useThemeStore } from './themeStore';

/** Stub `matchMedia` so the "system" branch is testable in jsdom (which has none by default). */
function stubSystem(dark: boolean): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('prefers-color-scheme: dark') ? dark : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    })),
  );
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
  document.documentElement.className = '';
  useThemeStore.setState({ mode: 'system', resolved: 'dark' });
});

afterEach(() => vi.unstubAllGlobals());

describe('themeStore', () => {
  it('stamps the resolved theme onto the document root', () => {
    applyTheme('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe('light');

    applyTheme('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('keeps the PWA theme-color meta in step so browser chrome follows', () => {
    const meta = document.createElement('meta');
    meta.setAttribute('name', 'theme-color');
    document.head.appendChild(meta);

    applyTheme('light');
    expect(meta.getAttribute('content')).toBe('#fbfbfd');
    applyTheme('dark');
    expect(meta.getAttribute('content')).toBe('#05070d');

    meta.remove();
  });

  it('resolves "system" against the OS preference', () => {
    stubSystem(true);
    expect(resolveTheme('system')).toBe('dark');
    stubSystem(false);
    expect(resolveTheme('system')).toBe('light');
  });

  it('resolves an explicit mode without consulting the OS', () => {
    stubSystem(true);
    expect(resolveTheme('light')).toBe('light');
    stubSystem(false);
    expect(resolveTheme('dark')).toBe('dark');
  });

  it('toggles relative to what is SHOWN, so it works from system mode too', () => {
    stubSystem(true); // OS is dark; mode is 'system' → resolved dark
    useThemeStore.getState().setMode('system');
    expect(useThemeStore.getState().resolved).toBe('dark');

    useThemeStore.getState().toggle();
    expect(useThemeStore.getState().mode).toBe('light');
    expect(useThemeStore.getState().resolved).toBe('light');
  });

  it('follows the OS only while in system mode', () => {
    stubSystem(true);
    useThemeStore.getState().setMode('system');

    stubSystem(false); // OS flips to light
    useThemeStore.getState().syncSystem();
    expect(useThemeStore.getState().resolved).toBe('light');

    // An explicit choice must NOT be overridden by a later OS change.
    useThemeStore.getState().setMode('dark');
    stubSystem(true);
    useThemeStore.getState().syncSystem();
    expect(useThemeStore.getState().resolved).toBe('dark');
  });

  it('persists only the mode — the resolved theme is derived', () => {
    stubSystem(false);
    useThemeStore.getState().setMode('dark');

    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    expect(raw).toBeTruthy();
    const persisted = JSON.parse(raw as string) as { state: Record<string, unknown> };
    expect(persisted.state.mode).toBe('dark');
    // Persisting `resolved` would let a stale value win over the live OS state on next boot.
    expect(persisted.state.resolved).toBeUndefined();
  });
});
