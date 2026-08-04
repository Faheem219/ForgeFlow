import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { META_THEME_COLOR } from '../../app/theme/palette';
import { THEME_STORAGE_KEY, applyTheme, resolveMode, useThemeStore } from './themeStore';

/** Stub `matchMedia` so the "system" branch is testable in jsdom deterministically. */
function stubSystem(prefersLight: boolean): void {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('prefers-color-scheme: light') ? prefersLight : false,
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

  it('keeps the theme-color meta in step so browser chrome follows the canvas', () => {
    const meta = document.createElement('meta');
    meta.setAttribute('name', 'theme-color');
    document.head.appendChild(meta);

    applyTheme('light');
    expect(meta.getAttribute('content')).toBe(META_THEME_COLOR.light);
    applyTheme('dark');
    expect(meta.getAttribute('content')).toBe(META_THEME_COLOR.dark);

    meta.remove();
  });

  it('resolves "system" against the OS preference', () => {
    stubSystem(false);
    expect(resolveMode('system')).toBe('dark');
    stubSystem(true);
    expect(resolveMode('system')).toBe('light');
  });

  it('resolves an explicit mode without consulting the OS', () => {
    stubSystem(true);
    expect(resolveMode('dark')).toBe('dark');
    stubSystem(false);
    expect(resolveMode('light')).toBe('light');
  });

  it('toggles relative to what is SHOWN, so it works from system mode too', () => {
    stubSystem(false); // OS is dark; mode is 'system' → resolved dark
    useThemeStore.getState().setMode('system');
    expect(useThemeStore.getState().resolved).toBe('dark');

    useThemeStore.getState().toggle();
    expect(useThemeStore.getState().mode).toBe('light');
    expect(useThemeStore.getState().resolved).toBe('light');
  });

  it('follows the OS only while in system mode', () => {
    stubSystem(false);
    useThemeStore.getState().setMode('system');

    stubSystem(true); // OS flips to light
    useThemeStore.getState().syncSystem();
    expect(useThemeStore.getState().resolved).toBe('light');

    // An explicit choice must NOT be overridden by a later OS change.
    useThemeStore.getState().setMode('dark');
    stubSystem(true);
    useThemeStore.getState().syncSystem();
    expect(useThemeStore.getState().resolved).toBe('dark');
  });

  it('stores the mode as a RAW string the index.html pre-paint script can read', () => {
    stubSystem(false);
    useThemeStore.getState().setMode('light');

    // Not JSON, no envelope — the inline script does localStorage.getItem(...) and compares
    // string equality. Wrapping this in a persist envelope would silently break the no-flash boot.
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
  });
});
