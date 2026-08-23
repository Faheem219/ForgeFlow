/**
 * Where workspace layouts are remembered (phase-61).
 *
 * Kept apart from the components so the storage rules are testable on their own — and because a
 * layout preference must never be able to take the workspace down: every access is guarded, since
 * a private-mode browser or a full quota throws on `localStorage`.
 */

export const LAYOUT_STORAGE_PREFIX = 'forgeflow:layout';

/** Per-project layout ids, so two projects can be arranged differently. */
export function layoutId(projectId: string, name: string): string {
  return `${LAYOUT_STORAGE_PREFIX}:${projectId}:${name}`;
}

export interface LayoutStorage {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
}

/** `localStorage` behind a guard; reads fail closed and writes fail silently. */
export function safeStorage(): LayoutStorage {
  return {
    getItem: (key) => {
      try {
        return window.localStorage.getItem(key);
      } catch {
        return null;
      }
    },
    setItem: (key, value) => {
      try {
        window.localStorage.setItem(key, value);
      } catch {
        /* a layout preference is not worth an exception */
      }
    },
  };
}

/** Forget every stored layout for a project — backs the "Reset layout" action. */
export function resetLayouts(projectId: string): void {
  try {
    const prefix = `${LAYOUT_STORAGE_PREFIX}:${projectId}:`;
    const keys: string[] = [];
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const key = window.localStorage.key(i);
      if (key && key.startsWith(prefix)) keys.push(key);
    }
    keys.forEach((key) => window.localStorage.removeItem(key));
  } catch {
    /* nothing to reset if storage is unavailable */
  }
}
