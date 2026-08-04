import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { Badge, Button, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import type { IntentAction, RequirementSpecInput, StageStatus } from '../../lib/types';
import { listStages, submitIntent } from '../workspace/api';
import { STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import { getRequirements, saveRequirements, specToInput, suggestCriteria } from './api';
import { RequirementsForm } from './RequirementsForm';
import { ReviewSummary } from './ReviewSummary';

// Requirements is "committed into the pipeline" once it's been accepted/reopened — only then can a
// save legally refine-stale downstream stages (the conductor rejects a refine on any other status).
const REOPENABLE: ReadonlySet<StageStatus> = new Set(['complete', 'skipped', 'stale']);

/**
 * Requirements stage panel (phase-26). Loads the latest spec; a review ⇄ edit toggle hosts the
 * guided form and the read-only summary. Proceed/Skip drive the conductor; on save, if a build
 * already exists, a refine intent marks test + downstream stale so they regenerate from the change.
 */
export function RequirementsPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<'review' | 'edit' | null>(null);

  const specQuery = useQuery({
    queryKey: ['requirements', projectId],
    queryFn: () => getRequirements(projectId),
  });
  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
  });

  const spec = specQuery.data ?? null;
  const status = stagesQuery.data?.find((s) => s.stage === 'requirements')?.status ?? 'empty';
  const buildStatus = stagesQuery.data?.find((s) => s.stage === 'build')?.status ?? 'empty';
  const buildExists = buildStatus === 'complete' || buildStatus === 'stale';

  // Review when a spec exists, edit otherwise — until the user explicitly toggles.
  const view: 'review' | 'edit' = mode ?? (spec ? 'review' : 'edit');

  const save = useMutation({
    mutationFn: (input: RequirementSpecInput) => saveRequirements(projectId, input),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['requirements', projectId] });
      // A change after a build invalidates anything generated from the old spec.
      if (buildExists && REOPENABLE.has(status)) {
        try {
          await submitIntent(projectId, { stage: 'requirements', action: 'refine' });
        } catch {
          // Conductor rejected the reopen (nothing to stale) — the save itself still stands.
        }
        void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
        void queryClient.invalidateQueries({ queryKey: ['artifacts', projectId] });
      }
      toast({ title: 'Requirements saved', variant: 'success' });
      setMode('review');
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Could not save requirements';
      toast({ title: 'Save failed', description, variant: 'error' });
    },
  });

  const intent = useMutation({
    mutationFn: (action: IntentAction) =>
      submitIntent(projectId, { stage: 'requirements', action }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['artifacts', projectId] });
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Stage action rejected';
      toast({ title: 'Requirements action rejected', description, variant: 'error' });
    },
  });

  const handleSuggest = useCallback(
    (name: string, description: string) => suggestCriteria(projectId, name, description),
    [projectId],
  );
  const handleSuggestError = useCallback(
    (message: string) => toast({ title: 'AI-suggest', description: message, variant: 'error' }),
    [],
  );

  const busy = intent.isPending;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          Requirements
          <span data-testid="requirements-status">
            <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
          </span>
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            data-testid="requirements-skip"
            onClick={() => intent.mutate('skip')}
          >
            Skip
          </Button>
          <Button
            size="sm"
            disabled={busy}
            data-testid="requirements-proceed"
            onClick={() => intent.mutate('proceed')}
          >
            Proceed
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        {specQuery.isLoading ? (
          <div className="flex h-full items-center justify-center">
            <Spinner />
          </div>
        ) : view === 'review' && spec ? (
          <ReviewSummary spec={spec} onEdit={() => setMode('edit')} />
        ) : (
          <RequirementsForm
            initialSpec={spec ? specToInput(spec) : null}
            isSaving={save.isPending}
            onSave={(input) => save.mutate(input)}
            onCancel={spec ? () => setMode('review') : undefined}
            onSuggest={handleSuggest}
            onSuggestError={handleSuggestError}
          />
        )}
      </div>
    </div>
  );
}

export default RequirementsPanel;
