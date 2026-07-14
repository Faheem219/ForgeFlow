import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import type { ArtifactDto, IntentAction, Stage, StageStateDto } from '../../lib/types';
import { listArtifacts, listStages, submitIntent } from './api';

export interface UseStage {
  stageState: StageStateDto | undefined;
  artifacts: ArtifactDto[];
  runAction: (action: IntentAction) => void;
  isActing: boolean;
}

/**
 * Per-stage view-model: the stage's current state, its artifacts, and a helper to run a
 * refine/proceed/skip intent (the conductor owns legality; errors surface as toasts).
 */
export function useStage(projectId: string, stage: Stage): UseStage {
  const queryClient = useQueryClient();

  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
  });

  const artifactsQuery = useQuery({
    queryKey: ['artifacts', projectId, stage],
    queryFn: () => listArtifacts(projectId, stage),
  });

  const action = useMutation({
    mutationFn: (act: IntentAction) => submitIntent(projectId, { stage, action: act }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['artifacts', projectId] });
    },
    onError: (err) => {
      const message = err instanceof ApiError ? err.message : 'Action failed';
      toast({ title: 'Stage action rejected', description: message, variant: 'error' });
    },
  });

  return {
    stageState: stagesQuery.data?.find((s) => s.stage === stage),
    artifacts: artifactsQuery.data ?? [],
    runAction: (act) => action.mutate(act),
    isActing: action.isPending,
  };
}
