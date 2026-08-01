import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Badge, Button, EmptyState, Spinner } from '../../components/ui';
import { toast } from '../../lib/stores/toastStore';
import type { EvalOutcome, EvalRecordDto, EvalSummary } from '../../lib/types';
import { fetchReport, getEvalSummary, listEvalRuns } from './api';
import { BarChart, Dumbbell, Histogram, Legend, StatTile, type DumbbellDatum } from './charts';
import { VIZ, num, pct } from './viz';

const OUTCOME_TONES: Record<EvalOutcome, 'success' | 'warning' | 'danger'> = {
  delivered: 'success',
  escalated: 'warning',
  failed: 'danger',
};

/** Group iteration counts into buckets, keeping the tail honest rather than truncating it. */
function iterationBuckets(records: EvalRecordDto[]): { label: string; count: number }[] {
  const buckets = [
    { label: '0', match: (n: number) => n === 0 },
    { label: '1', match: (n: number) => n === 1 },
    { label: '2', match: (n: number) => n === 2 },
    { label: '3', match: (n: number) => n === 3 },
    { label: '4', match: (n: number) => n === 4 },
    { label: '5+', match: (n: number) => n >= 5 },
  ];
  return buckets.map((b) => ({
    label: b.label,
    count: records.filter((r) => b.match(r.repair_iterations)).length,
  }));
}

function download(filename: string, text: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * The evidence view (D13): what the bounded repair loop actually adds, made legible.
 *
 * Every number here comes from the phase-44 records unchanged — the dashboard aggregates and draws,
 * it never recomputes a pass rate of its own. Where a spec produced no measurement it shows "—",
 * never a 0%, because a fabricated zero would understate the system exactly as badly as a
 * fabricated 100% would flatter it. The table below the charts carries every value, so nothing is
 * reachable only by hovering.
 */
export function EvalDashboard(): JSX.Element {
  const [source, setSource] = useState<string | undefined>(undefined);
  const [exporting, setExporting] = useState<string | null>(null);

  const summaryQuery = useQuery({
    queryKey: ['eval-summary', source],
    queryFn: () => getEvalSummary(source),
  });
  const runsQuery = useQuery({ queryKey: ['eval-runs'], queryFn: () => listEvalRuns() });

  const data = summaryQuery.data;
  const records = useMemo(() => data?.records ?? [], [data]);
  const summary: EvalSummary | undefined = data?.summary;

  async function exportReport(format: 'csv' | 'md'): Promise<void> {
    setExporting(format);
    try {
      const { filename, text } = await fetchReport(format, data?.source ?? null);
      download(filename, text, format === 'csv' ? 'text/csv' : 'text/markdown');
    } catch (err) {
      toast({
        title: 'Export failed',
        description: err instanceof Error ? err.message : 'Could not export the report',
        variant: 'error',
      });
    } finally {
      setExporting(null);
    }
  }

  if (summaryQuery.isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (!data?.available || !summary) {
    return (
      <EmptyState
        title="No evaluation runs yet"
        description="Run the harness to produce evidence: uv run python -m app.eval.runner --spec all"
      />
    );
  }

  const dumbbell: DumbbellDatum[] = records.map((r) => ({
    id: r.spec_id,
    label: r.spec_id,
    before: r.first_pass.rate,
    after: r.post_repair.rate,
  }));

  return (
    <div className="space-y-4" data-testid="eval-dashboard">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium text-primary">Evaluation</h2>
          <p className="text-xs text-faint">
            {summary.specs} specs · {summary.scored} scored
            {data.source ? ` · ${data.source}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {(runsQuery.data?.length ?? 0) > 1 ? (
            <select
              aria-label="Run"
              data-testid="eval-run-picker"
              value={data.source ?? ''}
              onChange={(e) => setSource(e.target.value || undefined)}
              className="rounded-lg border border-line bg-inset px-2 py-1.5 text-xs text-primary"
            >
              {runsQuery.data?.map((run) => (
                <option key={run.source} value={run.source}>
                  {run.source} ({run.specs})
                </option>
              ))}
            </select>
          ) : null}
          <Button
            size="sm"
            variant="secondary"
            data-testid="export-csv"
            loading={exporting === 'csv'}
            onClick={() => void exportReport('csv')}
          >
            Export CSV
          </Button>
          <Button
            size="sm"
            variant="secondary"
            data-testid="export-md"
            loading={exporting === 'md'}
            onClick={() => void exportReport('md')}
          >
            Export Markdown
          </Button>
        </div>
      </header>

      {/* The one number this view leads with, then its supporting tiles. */}
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile
          label="Repair delta"
          value={pct(summary.repair_delta.mean, true)}
          detail={`median ${pct(summary.repair_delta.median, true)} · ${summary.specs_improved_by_repair} of ${summary.scored} improved`}
          hero
          tone={(summary.repair_delta.mean ?? 0) > 0 ? 'good' : undefined}
        />
        <StatTile
          label="First-pass"
          value={pct(summary.first_pass.mean)}
          detail={`median ${pct(summary.first_pass.median)} · ${summary.green_first_pass} green`}
        />
        <StatTile
          label="Post-repair"
          value={pct(summary.post_repair.mean)}
          detail={`median ${pct(summary.post_repair.median)} · ${summary.green_post_repair} green`}
        />
        <StatTile
          label="Delivered"
          value={`${summary.outcomes.delivered}/${summary.specs}`}
          detail={`${summary.outcomes.escalated} escalated · ${summary.outcomes.failed} failed`}
        />
      </div>

      <section className="rounded-xl border border-subtle bg-surface p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-medium text-secondary">First-pass → post-repair, per spec</h3>
          <Legend
            items={[
              { label: 'First-pass', color: VIZ.context },
              { label: 'Post-repair', color: VIZ.accent },
            ]}
          />
        </div>
        <Dumbbell data={dumbbell} beforeLabel="First-pass" afterLabel="Post-repair" />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-subtle bg-surface p-3">
          <h3 className="mb-2 text-xs font-medium text-secondary">
            Repair iterations per spec
            <span className="ml-2 font-normal text-faint">
              median {num(summary.repair_iterations.median)}
            </span>
          </h3>
          <Histogram buckets={iterationBuckets(records)} />
        </section>

        <section className="rounded-xl border border-subtle bg-surface p-3">
          <h3 className="mb-2 text-xs font-medium text-secondary">
            Tokens per spec
            <span className="ml-2 font-normal text-faint">₹{num(summary.inr_cost.mean)} mean</span>
          </h3>
          <BarChart
            data={records.map((r) => ({ id: r.spec_id, label: r.spec_id, value: r.tokens }))}
            format={(v) => v.toLocaleString()}
            unit="tokens"
          />
        </section>
      </div>

      {summary.screenshot_to_url_seconds.count > 0 ? (
        <section className="rounded-xl border border-subtle bg-surface p-3">
          <h3 className="mb-2 text-xs font-medium text-secondary">
            Idea → live URL
            <span className="ml-2 font-normal text-faint">
              median {num(summary.screenshot_to_url_seconds.median)}s
            </span>
          </h3>
          <BarChart
            data={records
              .filter((r) => r.screenshot_to_url_seconds !== null)
              .map((r) => ({
                id: r.spec_id,
                label: r.spec_id,
                value: r.screenshot_to_url_seconds as number,
              }))}
            format={(v) => `${v.toFixed(0)}s`}
            unit="seconds"
          />
        </section>
      ) : null}

      {/* The table view: every value the charts show, reachable without hovering. */}
      <section className="rounded-xl border border-subtle bg-surface/60">
        <h3 className="border-b border-subtle px-3 py-2 text-xs font-medium text-secondary">
          Per spec
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full min-w-max text-left text-xs" data-testid="eval-table">
            <thead className="text-faint">
              <tr>
                {[
                  'Spec',
                  'Difficulty',
                  'Outcome',
                  'First-pass',
                  'Post-repair',
                  'Δ',
                  'Iterations',
                  'Regressions',
                  'Tokens',
                ].map((h) => (
                  <th key={h} className="px-3 py-1.5 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr
                  key={r.spec_id}
                  className="border-t border-subtle/60"
                  data-testid={`eval-row-${r.spec_id}`}
                >
                  <td className="px-3 py-1.5 font-mono text-primary">{r.spec_id}</td>
                  <td className="px-3 py-1.5 text-muted">{r.difficulty}</td>
                  <td className="px-3 py-1.5">
                    {/* Status never rides on color alone — the word is the label. */}
                    <Badge tone={OUTCOME_TONES[r.outcome]}>{r.outcome}</Badge>
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-secondary">
                    {pct(r.first_pass.rate)}
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-secondary">
                    {pct(r.post_repair.rate)}
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-secondary">
                    {pct(r.repair_delta, true)}
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-secondary">{r.repair_iterations}</td>
                  <td className="px-3 py-1.5 tabular-nums text-secondary">{r.regressions}</td>
                  <td className="px-3 py-1.5 tabular-nums text-secondary">
                    {r.tokens.toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export default EvalDashboard;
