import { create } from 'zustand';

/** How long a save's own `fs.write` echo is expected back before we stop suppressing it. */
const SELF_WRITE_TTL_MS = 5000;

export interface OpenFile {
  path: string;
  /** What the editor shows (may differ from `saved` while dirty). */
  content: string;
  /** Last content known to be on the server. */
  saved: string;
  /** True while the agent is streaming this file in — the editor is read-only. */
  generating: boolean;
}

export function isDirty(file: OpenFile | undefined): boolean {
  return file !== undefined && file.content !== file.saved;
}

interface IdeState {
  files: Record<string, OpenFile>;
  order: string[];
  active: string | null;
  /** Path awaiting a keep-mine / reload decision after a server write clashed with local edits. */
  conflict: string | null;
  selfWrites: Record<string, number>;

  openFile: (path: string, content: string) => void;
  closeFile: (path: string) => void;
  setActive: (path: string) => void;
  edit: (path: string, content: string) => void;
  markSaved: (path: string, content: string) => void;

  noteSelfWrite: (path: string) => void;
  consumeSelfWrite: (path: string) => boolean;

  streamIn: (path: string, content: string) => void;
  endGenerating: (path: string) => void;

  setConflict: (path: string | null) => void;
  reset: () => void;
}

export const useIdeStore = create<IdeState>((set, get) => ({
  files: {},
  order: [],
  active: null,
  conflict: null,
  selfWrites: {},

  openFile: (path, content) =>
    set((s) => ({
      files: { ...s.files, [path]: { path, content, saved: content, generating: false } },
      order: s.order.includes(path) ? s.order : [...s.order, path],
      active: path,
    })),

  closeFile: (path) =>
    set((s) => {
      const files = { ...s.files };
      delete files[path];
      const order = s.order.filter((p) => p !== path);
      const active = s.active === path ? (order[order.length - 1] ?? null) : s.active;
      return { files, order, active, conflict: s.conflict === path ? null : s.conflict };
    }),

  setActive: (path) => set({ active: path }),

  edit: (path, content) =>
    set((s) => {
      const file = s.files[path];
      // Ignore edits while the agent owns the file — the editor is read-only anyway.
      if (!file || file.generating) return s;
      return { files: { ...s.files, [path]: { ...file, content } } };
    }),

  markSaved: (path, content) =>
    set((s) => {
      const file = s.files[path];
      if (!file) return s;
      return { files: { ...s.files, [path]: { ...file, saved: content } } };
    }),

  noteSelfWrite: (path) => set((s) => ({ selfWrites: { ...s.selfWrites, [path]: Date.now() } })),

  consumeSelfWrite: (path) => {
    const at = get().selfWrites[path];
    if (at === undefined) return false;
    set((s) => {
      const selfWrites = { ...s.selfWrites };
      delete selfWrites[path];
      return { selfWrites };
    });
    return Date.now() - at < SELF_WRITE_TTL_MS;
  },

  streamIn: (path, content) =>
    set((s) => ({
      files: { ...s.files, [path]: { path, content, saved: content, generating: true } },
      order: s.order.includes(path) ? s.order : [...s.order, path],
      active: path, // focus the file being generated so the user watches it land
    })),

  endGenerating: (path) =>
    set((s) => {
      const file = s.files[path];
      if (!file) return s;
      return { files: { ...s.files, [path]: { ...file, generating: false } } };
    }),

  setConflict: (path) => set({ conflict: path }),

  reset: () => set({ files: {}, order: [], active: null, conflict: null, selfWrites: {} }),
}));
