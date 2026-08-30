import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Coins } from 'lucide-react';
import { useEffect, useRef } from 'react';

import { Badge, EmptyState, Skeleton } from '../../components/ui';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { BudgetDto, RunDto } from '../../lib/types';
import { getProjectCost, listRuns } from './api';
import { compactCount, exactCount, inr } from './format';

function duration(run: RunDto): string {
  if (run.duration_s === null) return 'running';
  return run.duration_s < 1 ? '<1s' : `${run.duration_s.toFixed(1)}s`;
}

/**
 * A cap's state, said in words rather than left to a colour. `null` cap is unlimited — which is a
 * different thing from "plenty left", so it reads differently.
 */
function BudgetBar({ label, budget }: { label: string; budget: BudgetDto }): JSX.Element {
  const uncapped = budget.cap_inr === null;
  const ratio = Math.min(1, budget.used_ratio ?? 0);
  const tone = budget.halted ? 'danger' : budget.warning ? 'warning' : 'neutral';

  return (
    <div data-testid={`budget-${label.toLowerCase()}`}>
      <div className="mb-1 flex items-center justify-between gap-2 text-xs">
        <span className="text-fg-muted">{label}</span>
        <span className="tabular-nums text-fg-muted">
          {inr(budget.spent_inr)}
          {uncapped ? (
            <span className="ml-1 text-fg-subtle">· no cap</span>
          ) : (
            <span className="ml-1 text-fg-subtle">of {inr(budget.cap_inr as number)}</span>
          )}
        </span>
      </div>
      {uncapped ? null : (
        <div className="h-1.5 overflow-hidden rounded-full bg-surface-raised">
          {/* Width transitions so a refresh reads as the bar moving, not a redraw. */}
          <div
            className={`h-full rounded-full transition-[width] duration-500 ease-out ${
              budget.halted ? 'bg-danger' : budget.warning ? 'bg-warning' : 'bg-brand'
            }`}
            style={{ width: `${ratio * 100}%` }}
          />
        </div>
      )}
      {budget.halted || budget.warning ? (
        <p className="mt-1 flex items-center gap-1.5 text-[11px]">
          <Badge tone={tone}>{budget.halted ? 'Halted' : 'Approaching cap'}</Badge>
          <span className="text-fg-muted">
            {budget.halted
              ? 'Runs are blocked until the cap is raised.'
              : `${inr(budget.headroom_inr ?? 0)} left before work halts.`}
          </span>
        </p>
      ) : null}
    </div>
  );
}

/**
 * Where the money went, and how much is left (phase-46, §7).
 *
 * Every number is summed from the same `Run` records the budget guard enforces against, so the
 * panel can never tell a user they have headroom the guard disagrees with. Budget state is stated
 * in words as well as colour — a halt is the moment a user most needs to know *why* work stopped,
 * and a red bar alone does not say that.
 */
export function CostPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const costQuery = useQuery({
    queryKey: ['project-cost', projectId],
    queryFn: () => getProjectCost(projectId),
  });
  const runsQuery = useQuery({
    queryKey: ['project-runs', projectId],
    queryFn: () => listRuns(projectId),
  });

  // Budget events land while work is running — refresh rather than show a stale number.
  const events = useRealtimeStore((s) => s.events);
  const consumed = useRef(0);
  useEffect(() => {
    if (events.length <= consumed.current) {
      consumed.current = events.length;
      return;
    }
    const fresh = events.slice(consumed.current);
    consumed.current = events.length;
    if (fresh.some((e) => e.event === 'budget.halt' || e.event === 'budget.warning')) {
      void queryClient.invalidateQueries({ queryKey: ['project-cost', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['project-runs', projectId] });
    }
  }, [events, projectId, queryClient]);

  if (costQuery.isLoading) {
    // Same bones as the resolved layout, so the numbers land without a jump.
    return (
      <div className="space-y-3" aria-hidden>
        <div className="grid grid-cols-3 gap-2">
          {Array.from({ length: 3 }, (_, i) => (
            <div key={i} className="space-y-1.5 rounded-lg border border-edge bg-surface px-2 py-2">
              <Skeleton className="h-2.5 w-12" />
              <Skeleton className="h-5 w-16" />
            </div>
          ))}
        </div>
        <Skeleton className="h-16 w-full rounded-lg" />
      </div>
    );
  }

  const cost = costQuery.data;
  if (!cost) {
    return (
      <EmptyState
        icon={<Coins aria-hidden className="h-6 w-6" strokeWidth={1.5} />}
        title="No cost data"
        description="Spend appears once an agent runs."
      />
    );
  }

  const runs = runsQuery.data ?? [];
  const kinds = Object.entries(cost.by_kind).sort((a, b) => b[1].inr - a[1].inr);

  return (
    <div className="space-y-3" data-testid="cost-panel">
      {/* `min-w-0` on each tile is what keeps this grid inside its pane: a grid track's automatic
          minimum is its content's width, so one long number pushes the other two tiles out rather
          than being clipped by its own box. */}
      <div className="grid grid-cols-3 gap-2">
        <div className="min-w-0 rounded-lg border border-edge bg-surface px-2 py-1.5">
          <p className="text-[11px] uppercase tracking-wide text-fg-subtle">Spend</p>
          <p
            className="truncate text-lg font-semibold tabular-nums text-fg"
            data-testid="cost-total"
            title={inr(cost.inr)}
          >
            {inr(cost.inr)}
          </p>
        </div>
        <div className="min-w-0 rounded-lg border border-edge bg-surface px-2 py-1.5">
          <p className="text-[11px] uppercase tracking-wide text-fg-subtle">Tokens</p>
          <p
            className="truncate text-lg font-semibold tabular-nums text-fg"
            data-testid="cost-tokens"
            title={exactCount(cost.tokens)}
          >
            {compactCount(cost.tokens)}
          </p>
        </div>
        <div className="min-w-0 rounded-lg border border-edge bg-surface px-2 py-1.5">
          <p className="text-[11px] uppercase tracking-wide text-fg-subtle">Runs</p>
          <p
            className="truncate text-lg font-semibold tabular-nums text-fg"
            data-testid="cost-runs"
            title={exactCount(cost.runs, 'runs')}
          >
            {compactCount(cost.runs)}
          </p>
        </div>
      </div>

      <div className="space-y-2 rounded-lg border border-edge bg-surface p-2">
        <BudgetBar label="Project" budget={cost.project} />
        <BudgetBar label="Global" budget={cost.global_budget} />
      </div>

      {kinds.length > 0 ? (
        <section>
          <h4 className="mb-1 text-[11px] uppercase tracking-wide text-fg-subtle">By work</h4>
          <ul className="space-y-1" data-testid="cost-by-kind">
            {kinds.map(([kind, stats]) => (
              <li
                key={kind}
                className="flex items-center justify-between gap-2 text-xs"
                data-testid={`kind-${kind}`}
              >
                <span className="min-w-0 flex-1 truncate font-mono text-fg-muted">{kind}</span>
                <span
                  className="whitespace-nowrap tabular-nums text-fg-muted"
                  title={exactCount(stats.tokens)}
                >
                  {inr(stats.inr)} · {compactCount(stats.tokens)} tok · {stats.runs}×
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h4 className="mb-1 text-[11px] uppercase tracking-wide text-fg-subtle">Runs</h4>
        {runs.length === 0 ? (
          <p className="text-xs text-fg-subtle">No runs yet.</p>
        ) : (
          <ul className="space-y-1" data-testid="run-explorer">
            {runs.map((run) => (
              <li
                key={run.id}
                className="flex items-center justify-between gap-2 rounded border border-edge/60 px-2 py-1 text-xs transition-colors hover:border-edge-strong hover:bg-surface-raised/50"
                data-testid={`run-${run.id}`}
              >
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-mono text-fg-muted">{run.kind}</span>
                  {run.outcome ? (
                    <span className="ml-1.5 text-fg-subtle">{run.outcome}</span>
                  ) : null}
                </span>
                <span className="whitespace-nowrap tabular-nums text-fg-subtle">
                  {inr(run.inr)} · {duration(run)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export default CostPanel;
