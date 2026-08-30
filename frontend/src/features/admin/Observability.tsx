import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { Badge, Skeleton } from '../../components/ui';
import { compactCount, exactCount } from '../cost/format';
import { getGlobalCost } from './api';
import type { Budget } from './types';

function inr(n: number): string {
  return `₹${n.toFixed(2)}`;
}

function BudgetLine({ budget }: { budget: Budget }): JSX.Element {
  const tone = budget.halted ? 'danger' : budget.warning ? 'warning' : 'success';
  const label = budget.halted ? 'halted' : budget.warning ? 'near cap' : 'ok';
  return (
    <div className="flex items-center gap-2" data-testid="global-budget">
      <Badge tone={tone}>{label}</Badge>
      <span className="text-sm text-fg-muted">
        {inr(budget.spent_inr)}
        {budget.cap_inr != null ? ` / ${inr(budget.cap_inr)}` : ' (uncapped)'}
      </span>
      {budget.used_ratio != null ? (
        <span className="text-xs text-fg-subtle">{Math.round(budget.used_ratio * 100)}% used</span>
      ) : null}
    </div>
  );
}

/** Platform-wide spend + budget caps (phase-46), with a link to the evaluation evidence (phase-45). */
export function Observability(): JSX.Element {
  const query = useQuery({ queryKey: ['admin-cost'], queryFn: getGlobalCost });

  if (query.isLoading) {
    return (
      <div className="space-y-4" aria-hidden>
        <Skeleton className="h-20 w-full rounded-xl" />
        <Skeleton className="h-28 w-full rounded-xl" />
      </div>
    );
  }
  const cost = query.data;
  if (!cost) {
    return <p className="text-sm text-fg-subtle">Cost data is unavailable.</p>;
  }

  return (
    <div className="space-y-4" data-testid="observability">
      <section className="space-y-2 rounded-xl border border-edge p-3">
        <h4 className="text-xs uppercase tracking-wide text-fg-subtle">Global budget</h4>
        <BudgetLine budget={cost.budget} />
      </section>

      <section className="rounded-xl border border-edge p-3">
        <h4 className="mb-2 text-xs uppercase tracking-wide text-fg-subtle">Platform spend</h4>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-fg-subtle">Total ₹</dt>
            <dd data-testid="cost-inr" className="text-fg">
              {inr(cost.inr)}
            </dd>
          </div>
          <div>
            <dt className="text-fg-subtle">Tokens</dt>
            <dd className="text-fg" title={exactCount(cost.tokens)}>
              {compactCount(cost.tokens)}
            </dd>
          </div>
          <div>
            <dt className="text-fg-subtle">Runs</dt>
            <dd className="text-fg">{cost.runs}</dd>
          </div>
          <div>
            <dt className="text-fg-subtle">Projects</dt>
            <dd className="text-fg">{cost.projects}</dd>
          </div>
        </dl>
      </section>

      <Link to="/eval" className="inline-block text-sm text-brand-text hover:underline">
        View evaluation evidence →
      </Link>
    </div>
  );
}

export default Observability;
