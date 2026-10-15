import { useQuery } from '@tanstack/react-query';
import { ChevronRight, Wrench } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Badge, Button, EmptyState, Spinner } from '../../components/ui';
import { cn } from '../../lib/cn';
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
  const width = Math.max(values.length * 30, 72);
  const height = 40;
  const pad = 4;
  const max = Math.max(...values, 1);
  const step = values.length > 1 ? (width - pad * 2) / (values.length - 1) : 0;
  const coords = values.map((v, i) => [
    pad + i * step,
    height - pad - (v / max) * (height - pad * 2),
  ]);
  const points = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  // The area under the line: the shrinking failure count reads as a shape that drains away.
  const area = `${pad},${height - pad} ${points} ${(pad + (values.length - 1) * step).toFixed(1)},${height - pad}`;
  const converging = values.length > 1 && values[values.length - 1] < values[0];

  return (
    <span className="flex items-center gap-3" data-testid="failing-sparkline">
      <svg width={width} height={height} role="img" aria-label="Failing tests per iteration">
        <line
          x1={pad}
          x2={width - pad}
          y1={height - pad}
          y2={height - pad}
          className="stroke-edge"
          strokeWidth="1"
        />
        {values.length > 1 ? (
          <polygon points={area} className={converging ? 'fill-success/15' : 'fill-warning/15'} />
        ) : null}
        <polyline
          points={points}
          fill="none"
          strokeWidth="1.75"
          strokeLinejoin="round"
          strokeLinecap="round"
          className={converging ? 'stroke-success' : 'stroke-warning'}
        />
        {coords.map(([x, y], i) => (
          <circle
            key={i}
            cx={x.toFixed(1)}
            cy={y.toFixed(1)}
            r={i === coords.length - 1 ? 3 : 2.25}
            className={cn(
              converging ? 'fill-success' : 'fill-warning',
              i !== coords.length - 1 && 'opacity-70',
            )}
          />
        ))}
      </svg>
      <span className="font-mono text-xs tabular-nums text-fg-muted" data-testid="failing-trail">
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
      className="mt-2 max-h-56 overflow-auto rounded-lg border border-edge bg-surface-sunken py-1.5 font-mono text-2xs leading-relaxed"
    >
      {lines.map((line, i) => {
        // Added/removed lines get a full-width wash, as in any review tool — the colour alone on
        // 11px text is easy to miss.
        const tone = line.startsWith('+')
          ? 'bg-success/[0.08] text-success'
          : line.startsWith('-')
            ? 'bg-danger/[0.08] text-danger'
            : line.startsWith('@@')
              ? 'text-brand-text'
              : 'text-fg-muted';
        return (
          <div key={i} className={cn('px-2.5', tone)}>
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
    <li className="px-3 py-2" data-testid="repair-attempt">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2 text-[13px] text-fg">
          <span className="flex h-5 w-6 shrink-0 items-center justify-center rounded bg-fg/[0.06] font-mono text-2xs tabular-nums text-fg-muted">
            {attempt.iteration}
          </span>
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
          <span
            className="truncate font-mono text-2xs text-fg-subtle"
            title={attempt.target_files.join(', ')}
          >
            {attempt.target_files.join(', ') || 'no files'}
          </span>
        </span>
        <Button
          size="xs"
          variant="ghost"
          disabled={!attempt.id}
          aria-expanded={open}
          data-testid={`toggle-diff-${attempt.iteration}`}
          onClick={() => setOpen((v) => !v)}
        >
          <ChevronRight
            aria-hidden
            className={cn('h-3 w-3 transition-transform duration-150', open && 'rotate-90')}
            strokeWidth={2}
          />
          {open ? 'Hide diff' : 'Diff'}
        </Button>
      </div>
      {open ? (
        diffQuery.isFetching ? (
          <div className="py-2 text-fg-faint">
            <Spinner size="sm" />
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
    <div className="space-y-3 rounded-xl border border-edge bg-surface p-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[13px] font-semibold text-fg">
          <Wrench aria-hidden className="h-3.5 w-3.5 text-fg-subtle" strokeWidth={1.75} />
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
              className="flex items-center gap-1.5 text-xs font-normal text-fg-muted"
              data-testid="repair-running"
            >
              <span className="text-brand-text">
                <Spinner size="sm" />
              </span>
              repairing…
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
            variant="secondary"
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
          className="py-8"
          title="No repair attempts yet"
          description="When tests fail, the bounded loop patches, re-runs, and reports here."
        />
      ) : (
        <>
          <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 rounded-lg bg-fg/[0.025] p-3">
            <div className="space-y-1.5">
              <p className="text-2xs font-medium text-fg-faint">Failing per iteration</p>
              <Sparkline values={trail} />
            </div>
            <dl className="grid grid-cols-3 gap-x-5 gap-y-2 text-xs tabular-nums text-fg">
              <div>
                <dt className="text-2xs text-fg-faint">Iterations</dt>
                <dd data-testid="repair-iterations">{metrics?.iterations ?? live.length}</dd>
              </div>
              <div>
                <dt className="text-2xs text-fg-faint">Regressions</dt>
                <dd data-testid="repair-regressions">{regressions}</dd>
              </div>
              <div>
                <dt className="text-2xs text-fg-faint">Tokens</dt>
                <dd data-testid="repair-tokens">{tokens}</dd>
              </div>
              <div>
                <dt className="text-2xs text-fg-faint">Cost</dt>
                <dd data-testid="repair-cost">₹{(metrics?.cost_inr ?? 0).toFixed(2)}</dd>
              </div>
              <div>
                <dt className="text-2xs text-fg-faint">Elapsed</dt>
                <dd>{(metrics?.wall_clock_s ?? 0).toFixed(1)}s</dd>
              </div>
            </dl>
          </div>

          {report && report.attempts.length > 0 ? (
            <ul
              className="divide-y divide-edge overflow-hidden rounded-lg border border-edge"
              data-testid="repair-attempts"
            >
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
