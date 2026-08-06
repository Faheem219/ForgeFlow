import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button, EmptyState } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import type {
  IntentRequest,
  Stage,
  StageStatus,
  TestResultDto,
  ValidationCycleDto,
  ValidationReason,
} from '../../lib/types';
import { Escalation } from '../testing/Escalation';
import { listStages, submitIntent } from '../workspace/api';
import { STATUS_LABELS, STATUS_TONES, prereqUnmet } from '../workspace/stageMeta';
import { getLatestValidation, getLiveRun } from './validateApi';

const REASON_LABELS: Record<ValidationReason, string> = {
  cycle_cap: 'Hit the repair/redeploy cycle cap',
  env_config: 'Looks like a configuration problem, not a code bug',
  repair_escalated: 'The repair loop could not fix it',
  redeploy_failed: 'The fix was made but the redeploy failed',
};

/** Live progress steps, in the order the controller emits them. */
const STEP_LABELS: Record<string, string> = {
  validating: 'Running tests against the live site',
  diagnosed: 'Diagnosing the failures',
  repairing: 'Repairing in Build',
  redeploying: 'Redeploying the fix',
  validated: 'Verified',
  escalated: 'Needs you',
};

function CycleRow({ cycle }: { cycle: ValidationCycleDto }): JSX.Element {
  const steps: { label: string; tone: 'neutral' | 'success' | 'danger' | 'warning' }[] = [
    cycle.green
      ? { label: 'Live tests passed', tone: 'success' as const }
      : {
          label: `${cycle.failing} live ${cycle.failing === 1 ? 'test' : 'tests'} failed`,
          tone: 'danger' as const,
        },
  ];
  if (cycle.diagnosis) {
    steps.push({
      label: cycle.diagnosis === 'env' ? 'Diagnosed: configuration' : 'Diagnosed: code bug',
      tone: cycle.diagnosis === 'env' ? 'warning' : 'neutral',
    });
  }
  if (cycle.repair_outcome) {
    steps.push({
      label: `Repair ${cycle.repair_outcome}`,
      tone: cycle.repair_outcome === 'fixed' ? 'success' : 'danger',
    });
  }
  if (cycle.redeploy_status) {
    steps.push({
      label: `Redeploy ${cycle.redeploy_status}`,
      tone: cycle.redeploy_status === 'live' ? 'success' : 'danger',
    });
  }

  return (
    <li className="rounded-lg border border-edge p-2" data-testid={`cycle-${cycle.index}`}>
      <div className="mb-1 text-xs uppercase tracking-wide text-fg-subtle">Cycle {cycle.index}</div>
      <div className="flex flex-wrap items-center gap-1.5">
        {steps.map((s) => (
          <Badge key={s.label} tone={s.tone}>
            {s.label}
          </Badge>
        ))}
      </div>
      {cycle.note ? <p className="mt-1 text-xs text-fg-subtle">{cycle.note}</p> : null}
    </li>
  );
}

function ResultRow({ result }: { result: TestResultDto }): JSX.Element {
  const failed = result.status === 'failed';
  return (
    <li className="flex items-start gap-2 border-b border-edge/60 py-1.5 last:border-0">
      <Badge tone={failed ? 'danger' : result.status === 'passed' ? 'success' : 'neutral'}>
        {result.status}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-fg">{result.name}</p>
        {result.criterion_id ? (
          <span className="font-mono text-[11px] text-fg-subtle">{result.criterion_id}</span>
        ) : null}
        {failed && result.failure?.message ? (
          <p className="mt-0.5 text-xs text-danger">{result.failure.message}</p>
        ) : null}
      </div>
    </li>
  );
}

/**
 * The Validate stage: is the deployed app actually working?
 *
 * On success this is where the run ends — a prominent, final live link. On failure it shows the
 * loop closing itself: diagnose → repair → redeploy → re-validate, cycle by cycle, and hands over
 * to a human when it is bounded out. Validation runs through the conductor, which owns the
 * validate⇐deploy prereq; the disabled button here is only a hint.
 */
export function Validate({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [liveStep, setLiveStep] = useState<string | null>(null);
  const consumed = useRef(0);

  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
  });
  const reportQuery = useQuery({
    queryKey: ['validation-latest', projectId],
    queryFn: () => getLatestValidation(projectId),
  });
  const liveRunQuery = useQuery({
    queryKey: ['validation-live-run', projectId],
    queryFn: () => getLiveRun(projectId),
  });

  const report = reportQuery.data ?? null;
  const liveRun = liveRunQuery.data ?? null;
  const stageStatus = stagesQuery.data?.find((s) => s.stage === 'validate')?.status ?? 'empty';
  const statusByStage = useMemo(() => {
    const map: Partial<Record<Stage, StageStatus>> = {};
    for (const s of stagesQuery.data ?? []) map[s.stage] = s.status;
    return map;
  }, [stagesQuery.data]);
  const missingPrereq = prereqUnmet('validate', statusByStage);

  // Follow validate.status while a cycle is in flight; refetch when it lands.
  const events = useRealtimeStore((s) => s.events);
  useEffect(() => {
    if (events.length <= consumed.current) {
      consumed.current = events.length;
      return;
    }
    const fresh = events.slice(consumed.current);
    consumed.current = events.length;
    const steps = fresh.filter((e) => e.event === 'validate.status');
    if (steps.length === 0) return;

    const last = steps[steps.length - 1].payload.step;
    setLiveStep(typeof last === 'string' ? last : null);
    if (last === 'validated' || last === 'escalated') {
      void queryClient.invalidateQueries({ queryKey: ['validation-latest', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['validation-live-run', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
    }
  }, [events, projectId, queryClient]);

  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    onSuccess: () => {
      setLiveStep(null);
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['validation-latest', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['validation-live-run', projectId] });
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Validation failed';
      toast({ title: 'Validation rejected', description, variant: 'error' });
    },
  });

  const disabledReason = missingPrereq ? `Validation needs a live deployment first.` : null;
  const busy =
    intent.isPending || Boolean(liveStep && !['validated', 'escalated'].includes(liveStep));
  const validated = report?.outcome === 'validated';

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          Live validation
          <span data-testid="validate-stage-status">
            <Badge tone={STATUS_TONES[stageStatus]}>{STATUS_LABELS[stageStatus]}</Badge>
          </span>
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={intent.isPending}
            data-testid="validate-skip"
            onClick={() => intent.mutate({ stage: 'validate', action: 'skip' })}
          >
            Skip
          </Button>
          <Button
            size="sm"
            disabled={Boolean(disabledReason) || busy}
            title={disabledReason ?? undefined}
            data-testid="validate-run"
            onClick={() => intent.mutate({ stage: 'validate', action: 'proceed' })}
          >
            {busy ? 'Validating…' : report ? 'Re-validate' : 'Validate'}
          </Button>
        </div>
      </header>

      {disabledReason ? (
        <p
          className="rounded-lg border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning"
          data-testid="validate-prereq-warning"
        >
          {disabledReason} Deploy the app, then come back to verify it actually works.
        </p>
      ) : null}

      {busy ? (
        // A live validation run is a full Playwright suite against the deployed URL — minutes with
        // nothing else on screen. The step label is kept as the caption so the mark says *which*
        // part of the run is going, not merely that something is.
        <div
          className="flex items-center justify-center rounded-xl border border-edge bg-surface py-6"
          data-testid="validate-progress"
        >
          <AgentWorking
            label={liveStep ? (STEP_LABELS[liveStep] ?? liveStep) : 'Validating the live URL'}
          />
        </div>
      ) : null}

      {/* The payoff: a verified deployment, and the link. */}
      {validated && report?.url ? (
        <section
          className="rounded-xl border border-success/30 bg-success/10 p-4"
          data-testid="validate-success"
        >
          <div className="mb-1 flex items-center gap-2">
            <Badge tone="success">Verified</Badge>
            <span className="text-sm text-success">Your app is live and its tests pass.</span>
          </div>
          <a
            href={report.url}
            target="_blank"
            rel="noreferrer"
            data-testid="final-live-link"
            className="block truncate text-lg font-medium text-success hover:underline"
          >
            {report.url}
          </a>
        </section>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-2">
        <div className="min-h-0 space-y-3 overflow-auto">
          <section>
            <h4 className="mb-1 text-xs uppercase tracking-wide text-fg-subtle">
              Live results {liveRun ? `(${liveRun.passed}/${liveRun.total} passing)` : ''}
            </h4>
            {!liveRun || liveRun.results.length === 0 ? (
              <EmptyState
                title="Not validated yet"
                description="Run validation to test the deployed app."
              />
            ) : (
              <ul data-testid="live-results">
                {liveRun.results.map((r) => (
                  <ResultRow key={`${r.file}::${r.name}`} result={r} />
                ))}
              </ul>
            )}
          </section>
        </div>

        <div className="min-h-0 space-y-3 overflow-auto">
          {report && report.cycles.length > 0 ? (
            <section>
              <h4 className="mb-1 text-xs uppercase tracking-wide text-fg-subtle">Timeline</h4>
              <ul className="space-y-2" data-testid="validate-timeline">
                {report.cycles.map((c) => (
                  <CycleRow key={c.index} cycle={c} />
                ))}
              </ul>
            </section>
          ) : null}

          {report && !validated ? (
            <section
              className="space-y-2 rounded-xl border border-warning/30 bg-warning/10 p-3"
              data-testid="validate-escalation"
            >
              <header className="flex items-center gap-2">
                <Badge tone="warning">Needs you</Badge>
                <span className="text-sm font-medium text-warning" data-testid="validate-reason">
                  {report.reason ? REASON_LABELS[report.reason] : 'Validation did not pass'}
                </span>
              </header>
              <p className="text-sm text-fg-muted" data-testid="validate-summary">
                {report.summary}
              </p>
            </section>
          ) : null}

          {/* When the inner repair loop escalated, its own UI carries the resume contract. */}
          {report?.repair_escalation ? (
            <Escalation projectId={projectId} escalation={report.repair_escalation} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default Validate;
