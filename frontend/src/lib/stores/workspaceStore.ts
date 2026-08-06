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

// The fallback stage before a project's own `current_stage` resolves — requirements, matching the
// backend's default entry stage (see STAGE_ORDER in features/workspace/stageMeta.ts).
const DEFAULT_STAGE: Stage = 'requirements';

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  projectId: null,
  activeStage: DEFAULT_STAGE,
  openProject: (projectId, activeStage) => set({ projectId, activeStage }),
  setActiveStage: (activeStage) => set({ activeStage }),
  reset: () => set({ projectId: null, activeStage: DEFAULT_STAGE }),
}));
