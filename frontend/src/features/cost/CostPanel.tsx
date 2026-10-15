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
      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-2 text-xs">
        <span className="text-fg-subtle">{label}</span>
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
        <div className="h-1 overflow-hidden rounded-full bg-fg/[0.07]">
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
        <p className="mt-1.5 flex flex-wrap items-center gap-1.5 text-2xs">
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
      <div className="space-y-2.5" aria-hidden>
        {Array.from({ length: 3 }, (_, i) => (
          <div key={i} className="flex items-center justify-between">
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3.5 w-16" />
          </div>
        ))}
        <Skeleton className="h-10 w-full rounded-lg" />
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
      {/* Label/value rows rather than three tiles: the rail is narrow and resizable, and side-by-side
          tiles truncated every figure to "₹…" at its default width. A row never does. */}
      <dl className="space-y-1">
        <div className="flex items-baseline justify-between gap-2">
          <dt className="text-xs text-fg-subtle">Spend</dt>
          <dd
            className="truncate text-[13px] font-semibold tabular-nums text-fg"
            data-testid="cost-total"
            title={inr(cost.inr)}
          >
            {inr(cost.inr)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <dt className="text-xs text-fg-subtle">Tokens</dt>
          <dd
            className="truncate text-[13px] tabular-nums text-fg-muted"
            data-testid="cost-tokens"
            title={exactCount(cost.tokens)}
          >
            {compactCount(cost.tokens)}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-2">
          <dt className="text-xs text-fg-subtle">Runs</dt>
          <dd
            className="truncate text-[13px] tabular-nums text-fg-muted"
            data-testid="cost-runs"
            title={exactCount(cost.runs, 'runs')}
          >
            {compactCount(cost.runs)}
          </dd>
        </div>
      </dl>

      <div className="space-y-3 rounded-lg bg-fg/[0.03] p-2.5">
        <BudgetBar label="Project" budget={cost.project} />
        <BudgetBar label="Global" budget={cost.global_budget} />
      </div>

      {kinds.length > 0 ? (
        <section>
          <h4 className="mb-1.5 text-2xs font-medium text-fg-faint">By work</h4>
          <ul className="space-y-1" data-testid="cost-by-kind">
            {kinds.map(([kind, stats]) => (
              // Two lines: the work kind gets the full width (it truncated to "codeg…" sharing a
              // line with three figures at rail width), the figures sit beneath it.
              <li key={kind} className="text-2xs" data-testid={`kind-${kind}`}>
                <span className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 truncate font-mono text-fg-muted" title={kind}>
                    {kind}
                  </span>
                  <span className="shrink-0 tabular-nums text-fg-muted">{inr(stats.inr)}</span>
                </span>
                <span className="block tabular-nums text-fg-faint" title={exactCount(stats.tokens)}>
                  {compactCount(stats.tokens)} tok · {stats.runs}×
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h4 className="mb-1.5 text-2xs font-medium text-fg-faint">Recent runs</h4>
        {runs.length === 0 ? (
          <p className="text-2xs text-fg-faint">No runs yet.</p>
        ) : (
          <ul className="space-y-1" data-testid="run-explorer">
            {runs.map((run) => (
              <li
                key={run.id}
                className="-mx-1.5 flex items-center justify-between gap-2 rounded-md px-1.5 py-1 text-2xs transition-colors hover:bg-fg/[0.04]"
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
