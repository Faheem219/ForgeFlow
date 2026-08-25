import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  LAYOUT_STORAGE_PREFIX,
  LAYOUT_STORAGE_ROOT,
  layoutId,
  resetLayouts,
  safeStorage,
} from './layoutStorage';

describe('layoutId', () => {
  it('scopes a layout to one project', () => {
    expect(layoutId('p1', 'shell')).not.toBe(layoutId('p2', 'shell'));
  });

  it('scopes a layout to one location within a project', () => {
    expect(layoutId('p1', 'shell')).not.toBe(layoutId('p1', 'build-center'));
  });

  it('namespaces every key so nothing else in storage is touched', () => {
    expect(layoutId('p1', 'shell').startsWith(LAYOUT_STORAGE_PREFIX)).toBe(true);
  });

  // Sizes used to be written as bare numbers, which the panel library reads as *pixels* — a saved
  // layout from before that fix describes a rail clamped to a 34px sliver. Restoring one would
  // silently reinstate the bug, so the version segment has to keep those keys unreachable.
  it('does not read layouts written before pane sizes were percentages', () => {
    expect(layoutId('p1', 'shell')).not.toBe(`${LAYOUT_STORAGE_ROOT}:p1:shell`);
  });
});

describe('safeStorage', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it('round-trips a layout', () => {
    const storage = safeStorage();
    storage.setItem(layoutId('p1', 'shell'), '[50,50]');

    expect(storage.getItem(layoutId('p1', 'shell'))).toBe('[50,50]');
  });

  it('returns null for a layout that was never saved', () => {
    expect(safeStorage().getItem(layoutId('p1', 'never'))).toBeNull();
  });

  // Private-mode browsers and a full quota both throw on access. A layout preference must never be
  // able to take the workspace down with it.
  it('reads fail closed when storage throws', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('SecurityError');
      },
      setItem: () => undefined,
      length: 0,
      key: () => null,
    });

    expect(safeStorage().getItem('anything')).toBeNull();
  });

  it('writes fail silently when storage throws', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => {
        throw new Error('QuotaExceededError');
      },
      length: 0,
      key: () => null,
    });

    expect(() => safeStorage().setItem('k', 'v')).not.toThrow();
  });
});

describe('resetLayouts', () => {
  beforeEach(() => window.localStorage.clear());

  it('clears every layout for the project', () => {
    window.localStorage.setItem(layoutId('p1', 'shell'), '[1]');
    window.localStorage.setItem(layoutId('p1', 'build-center'), '[2]');

    resetLayouts('p1');

    expect(window.localStorage.getItem(layoutId('p1', 'shell'))).toBeNull();
    expect(window.localStorage.getItem(layoutId('p1', 'build-center'))).toBeNull();
  });

  it('leaves other projects and unrelated keys alone', () => {
    window.localStorage.setItem(layoutId('p1', 'shell'), '[1]');
    window.localStorage.setItem(layoutId('p2', 'shell'), '[2]');
    window.localStorage.setItem('forgeflow:auth-token', 'secret');

    resetLayouts('p1');

    expect(window.localStorage.getItem(layoutId('p2', 'shell'))).toBe('[2]');
    expect(window.localStorage.getItem('forgeflow:auth-token')).toBe('secret');
  });

  // A reset is the one moment we can tidy up: sweep the retired versions too, or they sit in
  // storage forever.
  it('also clears layouts left behind by an earlier storage version', () => {
    window.localStorage.setItem(`${LAYOUT_STORAGE_ROOT}:p1:shell`, '[1]');

    resetLayouts('p1');

    expect(window.localStorage.getItem(`${LAYOUT_STORAGE_ROOT}:p1:shell`)).toBeNull();
  });

  it('is a no-op when there is nothing stored', () => {
    expect(() => resetLayouts('p1')).not.toThrow();
  });
});
