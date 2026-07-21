import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { Badge, EmptyState, Spinner } from '../../components/ui';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { BudgetDto, RunDto } from '../../lib/types';
import { getProjectCost, listRuns } from './api';
import { inr } from './format';

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
        <span className="text-slate-400">{label}</span>
        <span className="tabular-nums text-slate-300">
          {inr(budget.spent_inr)}
          {uncapped ? (
            <span className="ml-1 text-slate-500">· no cap</span>
          ) : (
            <span className="ml-1 text-slate-500">of {inr(budget.cap_inr as number)}</span>
          )}
        </span>
      </div>
      {uncapped ? null : (
        <div className="h-1.5 overflow-hidden rounded-full bg-slate-800">
          <div
            className={`h-full rounded-full ${
              budget.halted ? 'bg-red-500' : budget.warning ? 'bg-amber-500' : 'bg-brand-500'
            }`}
            style={{ width: `${ratio * 100}%` }}
          />
        </div>
      )}
      {budget.halted || budget.warning ? (
        <p className="mt-1 flex items-center gap-1.5 text-[11px]">
          <Badge tone={tone}>{budget.halted ? 'Halted' : 'Approaching cap'}</Badge>
          <span className="text-slate-400">
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
    return (
      <div className="flex h-24 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const cost = costQuery.data;
  if (!cost) {
    return <EmptyState title="No cost data" description="Spend appears once an agent runs." />;
  }

  const runs = runsQuery.data ?? [];
  const kinds = Object.entries(cost.by_kind).sort((a, b) => b[1].inr - a[1].inr);

  return (
    <div className="space-y-3" data-testid="cost-panel">
      <div className="grid grid-cols-3 gap-2">
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-2 py-1.5">
          <p className="text-[11px] uppercase tracking-wide text-slate-500">Spend</p>
          <p className="text-lg font-semibold text-slate-100" data-testid="cost-total">
            {inr(cost.inr)}
          </p>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-2 py-1.5">
          <p className="text-[11px] uppercase tracking-wide text-slate-500">Tokens</p>
          <p className="text-lg font-semibold text-slate-100" data-testid="cost-tokens">
            {cost.tokens.toLocaleString()}
          </p>
        </div>
        <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-2 py-1.5">
          <p className="text-[11px] uppercase tracking-wide text-slate-500">Runs</p>
          <p className="text-lg font-semibold text-slate-100" data-testid="cost-runs">
            {cost.runs}
          </p>
        </div>
      </div>

      <div className="space-y-2 rounded-lg border border-slate-800 bg-slate-900/40 p-2">
        <BudgetBar label="Project" budget={cost.project} />
        <BudgetBar label="Global" budget={cost.global_budget} />
      </div>

      {kinds.length > 0 ? (
        <section>
          <h4 className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">By work</h4>
          <ul className="space-y-1" data-testid="cost-by-kind">
            {kinds.map(([kind, stats]) => (
              <li
                key={kind}
                className="flex items-center justify-between gap-2 text-xs"
                data-testid={`kind-${kind}`}
              >
                <span className="truncate font-mono text-slate-300">{kind}</span>
                <span className="whitespace-nowrap tabular-nums text-slate-400">
                  {inr(stats.inr)} · {stats.tokens.toLocaleString()} tok · {stats.runs}×
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section>
        <h4 className="mb-1 text-[11px] uppercase tracking-wide text-slate-500">Runs</h4>
        {runs.length === 0 ? (
          <p className="text-xs text-slate-500">No runs yet.</p>
        ) : (
          <ul className="space-y-1" data-testid="run-explorer">
            {runs.map((run) => (
              <li
                key={run.id}
                className="flex items-center justify-between gap-2 rounded border border-slate-800/60 px-2 py-1 text-xs"
                data-testid={`run-${run.id}`}
              >
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-mono text-slate-300">{run.kind}</span>
                  {run.outcome ? (
                    <span className="ml-1.5 text-slate-500">{run.outcome}</span>
                  ) : null}
                </span>
                <span className="whitespace-nowrap tabular-nums text-slate-500">
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
