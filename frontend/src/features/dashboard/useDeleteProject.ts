import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';

import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import { useIdeStore } from '../ide/ideStore';
import { deleteProject, type ProjectDeleteResult } from './api';

/**
 * Delete a project, then scrub every trace of it from client state.
 *
 * The scrub is the part that is easy to get wrong. A deleted project leaves debris in four places,
 * and any one of them resurrects a ghost:
 *
 *  1. **TanStack Query cache** — `['project', id]`, `['stages', id]`, `['messages', id]`, and a
 *     dozen more. Left behind, a stale entry re-renders the dead project on the next mount, and
 *     background refetches hammer a 404 route.
 *  2. **The realtime socket** — still subscribed to a channel whose project is gone, reconnecting
 *     on a backoff forever.
 *  3. **The workspace store** — still pointing `projectId` at the deleted id.
 *  4. **The IDE store** — open tabs and file contents from a workspace that no longer exists.
 *
 * Query removal is keyed by *predicate* rather than an explicit list, because per-feature keys
 * (`['build-reports', id]`, `['repair-latest', id]`, …) are added by every new panel and an
 * enumerated list silently rots.
 */
export interface UseDeleteProject {
  remove: (projectId: string, options?: { onDeleted?: () => void }) => void;
  isDeleting: boolean;
}

export function useDeleteProject(): UseDeleteProject {
  const queryClient = useQueryClient();

  const scrub = useCallback(
    (projectId: string) => {
      // 2 + 3 + 4: only if the deleted project is the one currently open.
      const workspace = useWorkspaceStore.getState();
      if (workspace.projectId === projectId) {
        workspace.reset();
        useIdeStore.getState().reset();
      }
      if (useRealtimeStore.getState().channel === projectId) {
        useRealtimeStore.getState().disconnect();
      }

      // 1: drop every cached query whose key mentions this project id.
      queryClient.removeQueries({
        predicate: (query) => query.queryKey.some((part) => part === projectId),
      });
      void queryClient.invalidateQueries({ queryKey: ['projects'] });
    },
    [queryClient],
  );

  const mutation = useMutation({
    mutationFn: (projectId: string) => deleteProject(projectId),
    onSuccess: (result: ProjectDeleteResult) => {
      scrub(result.id);
      if (result.warnings.length > 0) {
        // Partial success is still success — but the user is told what outlived the project, since
        // an orphaned container or database costs them money and we cannot reclaim it silently.
        toast({
          title: 'Project deleted, with warnings',
          description: result.warnings.join(' · '),
          variant: 'warning',
          durationMs: 12000,
        });
      } else {
        toast({ title: 'Project deleted', variant: 'success' });
      }
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Could not delete the project';
      toast({ title: 'Delete failed', description, variant: 'error' });
    },
  });

  const remove = useCallback(
    (projectId: string, options?: { onDeleted?: () => void }) => {
      mutation.mutate(projectId, { onSuccess: () => options?.onDeleted?.() });
    },
    [mutation],
  );

  return { remove, isDeleting: mutation.isPending };
}
