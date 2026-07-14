import { create } from 'zustand';

import type { Stage } from '../types';

interface WorkspaceState {
  projectId: string | null;
  activeStage: Stage;
  /** Enter a project's workspace, seeding the active stage (usually its current_stage). */
  openProject: (projectId: string, activeStage: Stage) => void;
  setActiveStage: (stage: Stage) => void;
  reset: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  projectId: null,
  activeStage: 'design',
  openProject: (projectId, activeStage) => set({ projectId, activeStage }),
  setActiveStage: (activeStage) => set({ activeStage }),
  reset: () => set({ projectId: null, activeStage: 'design' }),
}));
