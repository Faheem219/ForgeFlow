import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface UiState {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebar: (collapsed: boolean) => void;
  // Workspace rails (the Stages/Cost rail and the Assistant conversation rail). Collapsing either
  // hands its width to the editor — the point of the layout overhaul.
  stagesOpen: boolean;
  assistantOpen: boolean;
  toggleStages: () => void;
  toggleAssistant: () => void;
  /** Bumped by global surfaces (command palette) to ask the dashboard to open its create dialog. */
  newProjectTick: number;
  requestNewProject: () => void;
}

/** Small shell-UI state (sidebar + workspace rail collapse), persisted across reloads. */
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
      newProjectTick: 0,
      requestNewProject: () => set((s) => ({ newProjectTick: s.newProjectTick + 1 })),
    }),
    { name: 'forgeflow-ui' },
  ),
);
