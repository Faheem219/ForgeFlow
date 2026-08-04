import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Badge, Button } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import { useWorkspaceStore } from '../../lib/stores/workspaceStore';
import type { RepairEscalation } from '../../lib/types';
import { submitIntent } from '../workspace/api';

const REASON_LABELS: Record<string, string> = {
  stalled: 'Stopped making progress',
  regressed: 'Patches kept breaking working tests',
  cap_reached: 'Hit the iteration cap',
  budget: 'Budget cap reached',
  blocked: 'Nothing safe to patch',
  cancelled: 'Cancelled',
};

/**
 * The human-in-the-loop moment (§9): the loop stopped short of green and says why. The user reads
 * the summary + what was already tried, then submits guidance which resumes via Build (phase-24) —
 * the controller itself names that contract in `escalation.resume`.
 */
export function Escalation({
  projectId,
  escalation,
  onResumed,
}: {
  projectId: string;
  escalation: RepairEscalation;
  onResumed?: () => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const setActiveStage = useWorkspaceStore((s) => s.setActiveStage);
  const [guidance, setGuidance] = useState('');

  const resume = useMutation({
    mutationFn: () =>
      submitIntent(projectId, {
        stage: escalation.resume.stage,
        action: escalation.resume.action,
        message: guidance.trim(),
      }),
    onSuccess: () => {
      setGuidance('');
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['build-reports', projectId] });
      // Guidance re-enters Build, so follow the pipeline there.
      setActiveStage(escalation.resume.stage);
      onResumed?.();
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Could not resume';
      toast({ title: 'Resume failed', description, variant: 'error' });
    },
  });

  return (
    <section
      className="space-y-3 rounded-xl border border-amber-700/50 bg-amber-950/20 p-3"
      data-testid="repair-escalation"
    >
      <header className="flex items-center gap-2">
        <Badge tone="warning">Needs you</Badge>
        <span className="text-sm font-medium text-amber-200" data-testid="escalation-reason">
          {REASON_LABELS[escalation.reason] ?? escalation.reason}
        </span>
      </header>

      <p className="text-sm text-slate-300" data-testid="escalation-summary">
        {escalation.summary}
      </p>

      {escalation.failing_tests.length > 0 ? (
        <div className="space-y-1">
          <h4 className="text-xs uppercase tracking-wide text-slate-500">Still failing</h4>
          <ul className="space-y-1" data-testid="escalation-failing">
            {escalation.failing_tests.map((t) => (
              <li key={`${t.file}::${t.name}`} className="text-xs text-slate-300">
                <span className="text-slate-200">{t.name}</span>
                {t.criterion_id ? (
                  <span className="ml-1 font-mono text-[11px] text-slate-500">
                    {t.criterion_id}
                  </span>
                ) : null}
                {t.message ? <span className="block text-slate-500">{t.message}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {escalation.diffs_tried.length > 0 ? (
        <div className="space-y-1">
          <h4 className="text-xs uppercase tracking-wide text-slate-500">
            Already tried ({escalation.diffs_tried.length})
          </h4>
          <ul className="space-y-0.5" data-testid="escalation-diffs">
            {escalation.diffs_tried.map((d) => (
              <li key={d.iteration} className="font-mono text-[11px] text-slate-500">
                #{d.iteration} {d.outcome ?? '—'} · {d.target_files.join(', ') || 'no files'}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <form
        className="space-y-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (guidance.trim()) resume.mutate();
        }}
      >
        <label className="block text-xs uppercase tracking-wide text-slate-500" htmlFor="guidance">
          Your guidance
        </label>
        <textarea
          id="guidance"
          aria-label="Guidance"
          data-testid="guidance-input"
          rows={3}
          value={guidance}
          onChange={(e) => setGuidance(e.target.value)}
          placeholder={escalation.resume.hint}
          disabled={resume.isPending}
          className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-brand-500 disabled:opacity-50"
        />
        <Button
          type="submit"
          size="sm"
          disabled={resume.isPending || !guidance.trim()}
          data-testid="submit-guidance"
        >
          {resume.isPending ? 'Resuming…' : 'Resume with guidance'}
        </Button>
      </form>
    </section>
  );
}

export default Escalation;
