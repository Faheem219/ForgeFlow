import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button, Skeleton } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import { toast } from '../../lib/stores/toastStore';
import { useTaskStream } from '../../lib/useTaskStream';
import type { IntentAction, RequirementSpecInput, StageStatus } from '../../lib/types';
import { listStages, submitIntent } from '../workspace/api';
import { nextStage, STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import {
  draftRequirements,
  getRequirements,
  saveRequirements,
  specToInput,
  suggestCriteria,
} from './api';
import { RequirementsForm } from './RequirementsForm';
import { ReviewSummary } from './ReviewSummary';

// Requirements is "committed into the pipeline" once it's been accepted/reopened — only then can a
// save legally refine-stale downstream stages (the conductor rejects a refine on any other status).
const REOPENABLE: ReadonlySet<StageStatus> = new Set(['complete', 'skipped', 'stale']);

/**
 * What to show when an AI-assist call fails: the server's own message when it sent one, else the
 * form's fallback wording. A provider error carries the fix ("no API key is configured — set it in
 * the admin panel"), which the generic "add features manually" text would otherwise bury.
 */
const assistMessage = (fallback: string, cause?: unknown): string =>
  cause instanceof ApiError ? cause.message : fallback;

/**
 * Requirements stage panel (phase-26). Loads the latest spec; a review ⇄ edit toggle hosts the
 * guided form and the read-only summary. Proceed/Skip drive the conductor; on save, if a build
 * already exists, a refine intent marks test + downstream stale so they regenerate from the change.
 */
export function RequirementsPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const setActiveStage = useWorkspaceStore((s) => s.setActiveStage);
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
    onSuccess: (_data, action) => {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['artifacts', projectId] });
      // Proceed/skip both mean "I'm done here for now" — release any explicit edit-mode toggle
      // (so a stale form doesn't linger once the stage has moved on) and jump the shared workspace
      // view to the next stage, instead of leaving the user staring at the same completed panel.
      if (action === 'proceed' || action === 'skip') {
        setMode(null);
        const next = nextStage('requirements');
        if (next) setActiveStage(next);
      }
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
    (message: string, cause?: unknown) =>
      toast({ title: 'AI-suggest', description: assistMessage(message, cause), variant: 'error' }),
    [],
  );
  const handleDraft = useCallback(
    (description: string) => draftRequirements(projectId, description),
    [projectId],
  );
  const handleDraftError = useCallback(
    (message: string, cause?: unknown) =>
      toast({ title: 'AI-draft', description: assistMessage(message, cause), variant: 'error' }),
    [],
  );

  /*
   * Driven by the realtime run, not by the request.
   *
   * `intent.isPending` only covers the POST; the agent keeps working long after it returns, and a
   * dropped connection would leave it pending forever. `useTaskStream` clears on the conductor's
   * terminal `run.finished`, which is what makes the indicator stop the moment the work is
   * actually done — and only then.
   */
  const task = useTaskStream(projectId, { pending: intent.isPending, stage: 'requirements' });
  const busy = task.busy;

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
        {busy ? (
          <div className="flex h-full items-center justify-center">
            <AgentWorking label={task.step ? `${task.step}` : 'Drafting the requirements'} />
          </div>
        ) : specQuery.isLoading ? (
          // Form-shaped ghost: label + field pairs, so the spec resolving doesn't jump the panel.
          <div className="space-y-4 p-1" aria-hidden>
            {Array.from({ length: 3 }, (_, i) => (
              <div key={i} className="space-y-1.5">
                <Skeleton className="h-3.5 w-28" />
                <Skeleton className="h-9 w-full" />
              </div>
            ))}
            <Skeleton className="h-24 w-full" />
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
            onDraft={handleDraft}
            onDraftError={handleDraftError}
          />
        )}
      </div>
    </div>
  );
}

export default RequirementsPanel;
