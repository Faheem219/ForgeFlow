import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CircleCheck, CircleAlert, CircleX, CircleMinus, ExternalLink, Radar } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { AgentWorking } from '../../components/AgentWorking';
import { Badge, Button, EmptyState } from '../../components/ui';
import { cn } from '../../lib/cn';
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
import { ResetStageButton } from '../workspace/StageReset';
import { STATUS_LABELS, STATUS_TONES, prereqUnmet } from '../workspace/stageMeta';
import { StageHeader } from '../workspace/StageHeader';
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
    <li className="group/cycle relative pb-3 pl-6 last:pb-0" data-testid={`cycle-${cycle.index}`}>
      {/* A timeline: one node per cycle on a vertical rail, green where the cycle ended green. */}
      <span
        aria-hidden
        className="absolute bottom-0 left-[7px] top-4 w-px bg-edge group-last/cycle:hidden"
      />
      <span
        aria-hidden
        className={cn(
          'absolute left-0 top-1 h-[15px] w-[15px] rounded-full border-2 bg-surface',
          cycle.green ? 'border-success' : 'border-danger/70',
        )}
      />
      <div className="mb-1.5 text-xs font-medium text-fg-muted">Cycle {cycle.index}</div>
      <div className="flex flex-wrap items-center gap-1">
        {steps.map((s) => (
          <Badge key={s.label} tone={s.tone}>
            {s.label}
          </Badge>
        ))}
      </div>
      {cycle.note ? (
        <p className="mt-1.5 text-xs leading-relaxed text-fg-subtle">{cycle.note}</p>
      ) : null}
    </li>
  );
}

const RESULT_ICONS = {
  passed: { icon: CircleCheck, tone: 'text-success' },
  failed: { icon: CircleX, tone: 'text-danger' },
  skipped: { icon: CircleMinus, tone: 'text-fg-faint' },
} as const;

function ResultRow({ result }: { result: TestResultDto }): JSX.Element {
  const failed = result.status === 'failed';
  const { icon: Icon, tone } = RESULT_ICONS[result.status];
  return (
    <li className="flex items-start gap-2.5 border-b border-edge px-3 py-2 last:border-0">
      <Icon aria-hidden className={cn('mt-0.5 h-3.5 w-3.5 shrink-0', tone)} strokeWidth={2} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[13px] text-fg">{result.name}</p>
        {result.criterion_id ? (
          <span className="font-mono text-2xs text-fg-faint">{result.criterion_id}</span>
        ) : null}
        {failed && result.failure?.message ? (
          <p className="mt-0.5 text-xs text-danger">{result.failure.message}</p>
        ) : null}
      </div>
      <Badge tone={failed ? 'danger' : result.status === 'passed' ? 'success' : 'neutral'}>
        {result.status}
      </Badge>
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
    <div className="flex h-full min-h-0 flex-col gap-2">
      <StageHeader
        title="Live validation"
        status={
          <span data-testid="validate-stage-status">
            <Badge tone={STATUS_TONES[stageStatus]}>{STATUS_LABELS[stageStatus]}</Badge>
          </span>
        }
        actions={
          <>
            <ResetStageButton
              projectId={projectId}
              stage="validate"
              status={stageStatus}
              disabled={busy}
            />
            <Button
              size="sm"
              variant="ghost"
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
              {busy ? null : <Radar aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />}
              {busy ? 'Validating…' : report ? 'Re-validate' : 'Validate'}
            </Button>
          </>
        }
      />

      {disabledReason ? (
        <p
          className="rounded-lg border border-warning/25 bg-warning/[0.08] px-3 py-2 text-xs leading-relaxed text-warning"
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
          className="relative flex items-center gap-3 overflow-hidden rounded-xl border border-edge bg-surface px-3 py-2"
          data-testid="validate-progress"
        >
          <div aria-hidden className="absolute inset-x-0 top-0 h-px overflow-hidden">
            <div className="h-full w-1/4 bg-gradient-to-r from-transparent via-brand-text to-transparent motion-safe:animate-ff-sweep" />
          </div>
          <AgentWorking
            compact
            label={liveStep ? (STEP_LABELS[liveStep] ?? liveStep) : 'Validating the live URL'}
          />
          <span className="text-[13px] font-medium text-fg">
            {liveStep ? (STEP_LABELS[liveStep] ?? liveStep) : 'Validating the live URL'}…
          </span>
        </div>
      ) : null}

      {/* The payoff: a verified deployment, and the link. */}
      {validated && report?.url ? (
        <section
          className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-edge bg-surface px-4 py-3.5"
          data-testid="validate-success"
        >
          <div className="flex min-w-0 items-center gap-3">
            <CircleCheck aria-hidden className="h-5 w-5 shrink-0 text-success" strokeWidth={2} />
            <div className="min-w-0">
              <p className="flex items-center gap-2 text-[13px] text-fg-muted">
                <Badge tone="success">Verified</Badge>
                Your app is live and its tests pass.
              </p>
              <a
                href={report.url}
                target="_blank"
                rel="noreferrer"
                data-testid="final-live-link"
                className="mt-0.5 block truncate font-mono text-[15px] font-medium text-fg underline-offset-4 hover:underline"
              >
                {report.url}
              </a>
            </div>
          </div>
          <a
            href={report.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-edge-strong/80 bg-surface px-2.5 text-xs font-medium text-fg shadow-raise transition-colors hover:bg-surface-raised"
          >
            Open app
            <ExternalLink aria-hidden className="h-3 w-3" strokeWidth={2} />
          </a>
        </section>
      ) : null}

      <div className="grid min-h-0 flex-1 gap-2 lg:grid-cols-2">
        <div className="min-h-0 space-y-3 overflow-auto">
          <section className="overflow-hidden rounded-xl border border-edge bg-surface">
            <h4 className="flex items-center justify-between border-b border-edge px-3 py-2 text-xs font-medium text-fg-muted">
              <span>Live results</span>
              {liveRun ? (
                <span className="tabular-nums text-fg-subtle">
                  {liveRun.passed}/{liveRun.total} passing
                </span>
              ) : null}
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
            <section className="rounded-xl border border-edge bg-surface p-3">
              <h4 className="mb-3 text-xs font-medium text-fg-muted">Timeline</h4>
              <ul data-testid="validate-timeline">
                {report.cycles.map((c) => (
                  <CycleRow key={c.index} cycle={c} />
                ))}
              </ul>
            </section>
          ) : null}

          {report && !validated ? (
            <section
              className="space-y-2 rounded-xl border border-warning/30 bg-surface p-3.5"
              data-testid="validate-escalation"
            >
              <header className="flex items-start gap-2.5">
                <CircleAlert
                  aria-hidden
                  className="mt-0.5 h-4 w-4 shrink-0 text-warning"
                  strokeWidth={2}
                />
                <div className="min-w-0">
                  <p className="text-2xs font-medium uppercase tracking-[0.06em] text-warning">
                    Needs you
                  </p>
                  <p className="text-[13px] font-semibold text-fg" data-testid="validate-reason">
                    {report.reason ? REASON_LABELS[report.reason] : 'Validation did not pass'}
                  </p>
                </div>
              </header>
              <p
                className="text-[13px] leading-relaxed text-fg-muted"
                data-testid="validate-summary"
              >
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
