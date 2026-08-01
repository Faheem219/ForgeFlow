import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * Shell + layout UI state, persisted across reloads.
 *
 * Two complementary controls over the workspace rails: `stagesOpen`/`assistantOpen` decide whether
 * a rail exists at all, and `panelSizes` decides how wide it is when it does. Collapse is the
 * "give the editor everything" gesture; the splitter is the fine adjustment.
 *
 * Both persist because a builder is a tool people arrange once and expect to stay arranged. Losing
 * your layout on every navigation is the difference between "an app with resizable panels" and "an
 * app you can lay out".
 *
 * Sizes are stored by a stable string key so a new splitter can be added without a store migration.
 */

export interface UiState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebar: (collapsed: boolean) => void;

  // Workspace rails (the Stages/Cost rail and the Assistant conversation rail). Collapsing either
  // hands its width to the editor — the point of the layout overhaul.
  stagesOpen: boolean;
  assistantOpen: boolean;
  toggleStages: () => void;
  toggleAssistant: () => void;

  /** Splitter sizes in px, keyed by `PanelKey`. */
  panelSizes: Record<string, number>;
  setPanelSize: (key: string, size: number) => void;
  resetPanelSizes: () => void;

  commandOpen: boolean;
  setCommandOpen: (open: boolean) => void;
}

/** Stable keys for every persisted splitter. Kept together so defaults are reviewable in one place. */
export const PanelKey = {
  workspaceNav: 'workspace.nav',
  workspaceChat: 'workspace.chat',
  workspaceTerminal: 'workspace.terminal',
  ideTree: 'ide.tree',
  buildPreview: 'build.preview',
  buildReport: 'build.report',
  dataCollections: 'data.collections',
} as const;

export const PANEL_DEFAULTS: Record<string, number> = {
  [PanelKey.workspaceNav]: 264,
  [PanelKey.workspaceChat]: 360,
  [PanelKey.workspaceTerminal]: 260,
  [PanelKey.ideTree]: 232,
  [PanelKey.buildPreview]: 480,
  [PanelKey.buildReport]: 340,
  [PanelKey.dataCollections]: 240,
};

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebar: (collapsed) => set({ sidebarCollapsed: collapsed }),

      stagesOpen: true,
      assistantOpen: true,
      toggleStages: () => set((s) => ({ stagesOpen: !s.stagesOpen })),
      toggleAssistant: () => set((s) => ({ assistantOpen: !s.assistantOpen })),

      panelSizes: {},
      setPanelSize: (key, size) =>
        set((s) => ({ panelSizes: { ...s.panelSizes, [key]: Math.round(size) } })),
      resetPanelSizes: () => set({ panelSizes: {} }),

      commandOpen: false,
      setCommandOpen: (commandOpen) => set({ commandOpen }),
    }),
    {
      name: 'forgeflow-ui',
      // The command palette is ephemeral — persisting it would reopen it on every reload.
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        stagesOpen: s.stagesOpen,
        assistantOpen: s.assistantOpen,
        panelSizes: s.panelSizes,
      }),
    },
  ),
);

/** Current size for a splitter, falling back to its default. */
export function usePanelSize(key: string): number {
  return useUiStore((s) => s.panelSizes[key] ?? PANEL_DEFAULTS[key] ?? 280);
}
