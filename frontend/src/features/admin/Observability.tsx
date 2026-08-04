import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';

import { Badge, Spinner } from '../../components/ui';
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
      <span className="text-sm text-slate-300">
        {inr(budget.spent_inr)}
        {budget.cap_inr != null ? ` / ${inr(budget.cap_inr)}` : ' (uncapped)'}
      </span>
      {budget.used_ratio != null ? (
        <span className="text-xs text-slate-500">{Math.round(budget.used_ratio * 100)}% used</span>
      ) : null}
    </div>
  );
}

/** Platform-wide spend + budget caps (phase-46), with a link to the evaluation evidence (phase-45). */
export function Observability(): JSX.Element {
  const query = useQuery({ queryKey: ['admin-cost'], queryFn: getGlobalCost });

  if (query.isLoading) {
    return (
      <div className="flex justify-center py-6">
        <Spinner />
      </div>
    );
  }
  const cost = query.data;
  if (!cost) {
    return <p className="text-sm text-slate-500">Cost data is unavailable.</p>;
  }

  return (
    <div className="space-y-4" data-testid="observability">
      <section className="space-y-2 rounded-xl border border-slate-800 p-3">
        <h4 className="text-xs uppercase tracking-wide text-slate-500">Global budget</h4>
        <BudgetLine budget={cost.budget} />
      </section>

      <section className="rounded-xl border border-slate-800 p-3">
        <h4 className="mb-2 text-xs uppercase tracking-wide text-slate-500">Platform spend</h4>
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-slate-500">Total ₹</dt>
            <dd data-testid="cost-inr" className="text-slate-200">
              {inr(cost.inr)}
            </dd>
          </div>
          <div>
            <dt className="text-slate-500">Tokens</dt>
            <dd className="text-slate-200">{cost.tokens.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Runs</dt>
            <dd className="text-slate-200">{cost.runs}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Projects</dt>
            <dd className="text-slate-200">{cost.projects}</dd>
          </div>
        </dl>
      </section>

      <Link to="/eval" className="inline-block text-sm text-brand-300 hover:underline">
        View evaluation evidence →
      </Link>
    </div>
  );
}

export default Observability;
