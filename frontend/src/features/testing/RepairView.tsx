import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Badge, Button, EmptyState, Spinner } from '../../components/ui';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { RepairAttemptDto, RepairLoopDto } from '../../lib/types';
import { getAttemptDiff } from './api';

const OUTCOME_TONES = {
  fixed: 'success',
  no_progress: 'warning',
  regressed: 'danger',
} as const;

/** One live iteration as it arrives over the realtime channel (phase-31 `repair.iteration`). */
export interface LiveIteration {
  i: number;
  failing_count: number;
  regressions: number;
  tokens: number;
  outcome?: string;
  files?: string[];
}

/**
 * The convergence signal, foregrounded on purpose: a descending line means the loop is working.
 * Rendered as inline SVG so it costs nothing and works in any theme.
 */
export function Sparkline({ values }: { values: number[] }): JSX.Element {
  if (values.length === 0) {
    return <span className="text-xs text-fg-subtle">no iterations yet</span>;
  }
  const width = Math.max(values.length * 18, 36);
  const height = 28;
  const max = Math.max(...values, 1);
  const step = values.length > 1 ? width / (values.length - 1) : width;
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - (v / max) * (height - 4) - 2).toFixed(1)}`)
    .join(' ');
  const converging = values.length > 1 && values[values.length - 1] < values[0];

  return (
    <span className="flex items-center gap-2" data-testid="failing-sparkline">
      <svg width={width} height={height} role="img" aria-label="Failing tests per iteration">
        <polyline
          points={points}
          fill="none"
          strokeWidth="2"
          className={converging ? 'stroke-success' : 'stroke-warning'}
        />
        {values.map((v, i) => (
          <circle
            key={i}
            cx={(i * step).toFixed(1)}
            cy={(height - (v / max) * (height - 4) - 2).toFixed(1)}
            r="2.5"
            className={converging ? 'fill-success' : 'fill-warning'}
          />
        ))}
      </svg>
      <span className="font-mono text-xs text-fg-muted" data-testid="failing-trail">
        {values.join(' → ')}
      </span>
    </span>
  );
}

function DiffBlock({ diff }: { diff: string }): JSX.Element {
  // A unified diff is what the agent actually applied, so render it as such (line-tinted) rather
  // than reconstructing two documents for a side-by-side editor.
  const lines = diff.split('\n');
  return (
    <pre
      data-testid="attempt-diff"
      className="max-h-56 overflow-auto rounded-lg border border-edge bg-surface-sunken p-2 font-mono text-[11px] leading-relaxed"
    >
      {lines.map((line, i) => {
        const tone = line.startsWith('+')
          ? 'text-success'
          : line.startsWith('-')
            ? 'text-danger'
            : line.startsWith('@@')
              ? 'text-brand-text'
              : 'text-fg-muted';
        return (
          <div key={i} className={tone}>
            {line || ' '}
          </div>
        );
      })}
    </pre>
  );
}

function AttemptRow({
  projectId,
  attempt,
}: {
  projectId: string;
  attempt: RepairAttemptDto;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const diffQuery = useQuery({
    queryKey: ['repair-diff', attempt.id],
    queryFn: () => getAttemptDiff(projectId, attempt.id as string),
    enabled: open && Boolean(attempt.id),
  });

  return (
    <li className="rounded-lg border border-edge p-2" data-testid="repair-attempt">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm text-fg">
          <span className="font-mono text-xs text-fg-subtle">#{attempt.iteration}</span>
          {attempt.outcome ? (
            <Badge tone={OUTCOME_TONES[attempt.outcome]}>{attempt.outcome}</Badge>
          ) : null}
          {/* The diff below is still readable, but it is not what the workspace ended up with. */}
          {attempt.reverted ? (
            <span
              data-testid="attempt-reverted"
              title="Undone — it broke a passing test and fixed nothing"
            >
              <Badge tone="neutral">reverted</Badge>
            </span>
          ) : null}
          <span className="font-mono text-[11px] text-fg-subtle">
            {attempt.target_files.join(', ') || 'no files'}
          </span>
        </span>
        <Button
          size="sm"
          variant="ghost"
          disabled={!attempt.id}
          data-testid={`toggle-diff-${attempt.iteration}`}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? 'Hide diff' : 'Diff'}
        </Button>
      </div>
      {open ? (
        diffQuery.isFetching ? (
          <div className="py-2">
            <Spinner />
          </div>
        ) : (
          <DiffBlock diff={diffQuery.data?.diff || '(no diff recorded)'} />
        )
      ) : null}
    </li>
  );
}

export function RepairView({
  projectId,
  report,
  running,
  onRepair,
  onCancel,
  canRepair,
}: {
  projectId: string;
  report: RepairLoopDto | null;
  running: boolean;
  onRepair: () => void;
  onCancel: () => void;
  canRepair: boolean;
}): JSX.Element {
  // While the loop runs, the live events are the source of truth; afterwards the persisted
  // report is (it survives a reload).
  const events = useRealtimeStore((s) => s.events);
  const live = useMemo<LiveIteration[]>(() => {
    const out: LiveIteration[] = [];
    for (const e of events) {
      if (e.event === 'repair.iteration' && e.project_id === projectId) {
        const iteration = e.payload as unknown as LiveIteration;
        // The event ring outlives a loop, so "Repair again" would otherwise graft its first
        // iteration onto the previous loop's trail. Each loop counts from 1, so a non-increasing
        // `i` means a new one started — drop what came before it.
        const previous = out.at(-1);
        if (previous && iteration.i <= previous.i) out.length = 0;
        out.push(iteration);
      }
    }
    return out;
  }, [events, projectId]);

  const trail =
    running && live.length > 0
      ? live.map((it) => it.failing_count)
      : (report?.metrics.failing_by_iteration ?? live.map((it) => it.failing_count));

  const metrics = report?.metrics;
  const tokens = metrics?.tokens_spent ?? live.at(-1)?.tokens ?? 0;
  const regressions = metrics?.regressions_introduced ?? live.at(-1)?.regressions ?? 0;

  return (
    <div className="space-y-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          Self-healing repair
          {report ? (
            <span data-testid="repair-outcome">
              <Badge tone={report.outcome === 'fixed' ? 'success' : 'warning'}>
                {report.outcome}
              </Badge>
            </span>
          ) : null}
          {running ? (
            <span
              className="flex items-center gap-1 text-xs text-fg-muted"
              data-testid="repair-running"
            >
              <Spinner /> repairing…
            </span>
          ) : null}
        </span>
        <div className="flex gap-2">
          {running ? (
            <Button size="sm" variant="secondary" data-testid="cancel-repair" onClick={onCancel}>
              Cancel
            </Button>
          ) : null}
          <Button
            size="sm"
            disabled={running || !canRepair}
            data-testid="run-repair"
            onClick={onRepair}
            title={canRepair ? undefined : 'Repair needs a failing test run'}
          >
            {report ? 'Repair again' : 'Repair'}
          </Button>
        </div>
      </header>

      {trail.length === 0 && !running ? (
        <EmptyState
          title="No repair attempts yet"
          description="When tests fail, the bounded loop patches, re-runs, and reports here."
        />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-4 rounded-xl border border-edge p-3">
            <div className="space-y-1">
              <p className="text-xs uppercase tracking-wide text-fg-subtle">
                Failing per iteration
              </p>
              <Sparkline values={trail} />
            </div>
            <dl className="flex gap-4 text-xs text-fg-muted">
              <div>
                <dt className="text-fg-subtle">Iterations</dt>
                <dd data-testid="repair-iterations">{metrics?.iterations ?? live.length}</dd>
              </div>
              <div>
                <dt className="text-fg-subtle">Regressions</dt>
                <dd data-testid="repair-regressions">{regressions}</dd>
              </div>
              <div>
                <dt className="text-fg-subtle">Tokens</dt>
                <dd data-testid="repair-tokens">{tokens}</dd>
              </div>
              <div>
                <dt className="text-fg-subtle">Cost</dt>
                <dd data-testid="repair-cost">₹{(metrics?.cost_inr ?? 0).toFixed(2)}</dd>
              </div>
              <div>
                <dt className="text-fg-subtle">Elapsed</dt>
                <dd>{(metrics?.wall_clock_s ?? 0).toFixed(1)}s</dd>
              </div>
            </dl>
          </div>

          {report && report.attempts.length > 0 ? (
            <ul className="space-y-2" data-testid="repair-attempts">
              {report.attempts.map((attempt) => (
                <AttemptRow key={attempt.iteration} projectId={projectId} attempt={attempt} />
              ))}
            </ul>
          ) : null}
        </>
      )}
    </div>
  );
}

export default RepairView;
