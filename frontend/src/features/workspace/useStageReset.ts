import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';

import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import type { IntentResponse, Stage } from '../../lib/types';
import { submitIntent } from './api';
import { STAGE_LABELS } from './stageMeta';

/**
 * Every per-project query a reset can change. A status-only reset moves a badge, but a deploy reset
 * changes what three panels hydrate from (the deployment, its logs, and the live validation), so
 * all of them are refreshed rather than leaving one panel showing what was just taken down.
 */
const RESET_QUERY_KEYS = [
  'stages',
  'messages',
  'artifacts',
  'build-reports',
  'activity',
  'deployment-latest',
  'deploy-logs',
  'validation-latest',
  'validation-live-run',
] as const;

export function refreshAfterReset(queryClient: QueryClient, projectId: string): void {
  for (const key of RESET_QUERY_KEYS) {
    void queryClient.invalidateQueries({ queryKey: [key, projectId] });
  }
}

/** A deploy reset destroys the live site, so it asks first; every other reset is a badge change. */
export function resetNeedsConfirm(stage: Stage): boolean {
  return stage === 'deploy';
}

function announceReset(stage: Stage, response: IntentResponse): void {
  toast({
    title: `${STAGE_LABELS[stage]} reset`,
    // The conductor's reply says exactly what happened (and anything a provider refused to remove).
    description: response.messages[0]?.content,
    variant: 'success',
  });
}

/** The reset intent, plus the refresh + announcement every caller wants after it. */
export function useStageReset(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (stage: Stage) => submitIntent(projectId, { stage, action: 'reset' }),
    onSuccess: (response, stage) => {
      refreshAfterReset(queryClient, projectId);
      announceReset(stage, response);
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Reset failed';
      toast({ title: 'Reset rejected', description, variant: 'error' });
    },
  });
}
