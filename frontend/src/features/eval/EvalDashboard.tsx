import { useQuery } from '@tanstack/react-query';
import { FileDown, FlaskConical } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';

import { Badge, Button, EmptyState, Skeleton } from '../../components/ui';
import { compactCount } from '../cost/format';
import { EASE_OUT, gsap, motionOK, useGSAP } from '../../lib/gsap';
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
  const scope = useRef<HTMLDivElement | null>(null);

  const summaryQuery = useQuery({
    queryKey: ['eval-summary', source],
    queryFn: () => getEvalSummary(source),
  });
  const runsQuery = useQuery({ queryKey: ['eval-runs'], queryFn: () => listEvalRuns() });

  const data = summaryQuery.data;
  const records = useMemo(() => data?.records ?? [], [data]);
  const summary: EvalSummary | undefined = data?.summary;

  // Tiles and chart panels rise in once when the evidence resolves — same beat as the dashboard.
  useGSAP(
    () => {
      if (!motionOK() || !summaryQuery.isSuccess) return;
      gsap.from('[data-eval-section]', {
        opacity: 0,
        y: 16,
        duration: 0.45,
        ease: EASE_OUT,
        stagger: 0.06,
        clearProps: 'all',
      });
    },
    { scope, dependencies: [summaryQuery.isSuccess] },
  );

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
    // Ghost of the real layout — header, stat tiles, a chart panel — so the resolve doesn't jump.
    return (
      <div className="space-y-4" aria-hidden>
        <div className="space-y-1.5">
          <Skeleton className="h-7 w-40" />
          <Skeleton className="h-3.5 w-56" />
        </div>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="space-y-2 rounded-xl border border-edge bg-surface px-3 py-3">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-6 w-16" />
              <Skeleton className="h-3 w-28" />
            </div>
          ))}
        </div>
        <Skeleton className="h-52 w-full rounded-xl" />
      </div>
    );
  }

  if (!data?.available || !summary) {
    return (
      <EmptyState
        icon={<FlaskConical aria-hidden className="h-6 w-6" strokeWidth={1.5} />}
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
    <div ref={scope} className="mx-auto max-w-7xl space-y-4" data-testid="eval-dashboard">
      <header data-eval-section className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight text-fg">Evaluation</h2>
          <p className="mt-0.5 text-sm text-fg-muted">
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
              className="rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1.5 text-xs text-fg outline-none transition-colors focus:border-brand"
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
            <FileDown aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
            Export CSV
          </Button>
          <Button
            size="sm"
            variant="secondary"
            data-testid="export-md"
            loading={exporting === 'md'}
            onClick={() => void exportReport('md')}
          >
            <FileDown aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
            Export Markdown
          </Button>
        </div>
      </header>

      {/* The one number this view leads with, then its supporting tiles. */}
      <div data-eval-section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
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

      <section data-eval-section className="rounded-xl border border-edge bg-surface-overlay p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-xs font-medium text-fg-muted">First-pass → post-repair, per spec</h3>
          <Legend
            items={[
              { label: 'First-pass', color: VIZ.context },
              { label: 'Post-repair', color: VIZ.accent },
            ]}
          />
        </div>
        <Dumbbell data={dumbbell} beforeLabel="First-pass" afterLabel="Post-repair" />
      </section>

      <div data-eval-section className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-edge bg-surface-overlay p-3">
          <h3 className="mb-2 text-xs font-medium text-fg-muted">
            Repair iterations per spec
            <span className="ml-2 font-normal text-fg-subtle">
              median {num(summary.repair_iterations.median)}
            </span>
          </h3>
          <Histogram buckets={iterationBuckets(records)} />
        </section>

        <section className="rounded-xl border border-edge bg-surface-overlay p-3">
          <h3 className="mb-2 text-xs font-medium text-fg-muted">
            Tokens per spec
            <span className="ml-2 font-normal text-fg-subtle">
              ₹{num(summary.inr_cost.mean)} mean
            </span>
          </h3>
          <BarChart
            data={records.map((r) => ({ id: r.spec_id, label: r.spec_id, value: r.tokens }))}
            format={(v) => compactCount(v)}
            unit="tokens"
          />
        </section>
      </div>

      {summary.screenshot_to_url_seconds.count > 0 ? (
        <section data-eval-section className="rounded-xl border border-edge bg-surface-overlay p-3">
          <h3 className="mb-2 text-xs font-medium text-fg-muted">
            Idea → live URL
            <span className="ml-2 font-normal text-fg-subtle">
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
      <section data-eval-section className="rounded-xl border border-edge bg-surface">
        <h3 className="border-b border-edge px-3 py-2 text-xs font-medium text-fg-muted">
          Per spec
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full min-w-max text-left text-xs" data-testid="eval-table">
            <thead className="text-fg-subtle">
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
                  className="border-t border-edge/60 transition-colors hover:bg-surface-raised/50"
                  data-testid={`eval-row-${r.spec_id}`}
                >
                  <td className="px-3 py-1.5 font-mono text-fg">{r.spec_id}</td>
                  <td className="px-3 py-1.5 text-fg-muted">{r.difficulty}</td>
                  <td className="px-3 py-1.5">
                    {/* Status never rides on color alone — the word is the label. */}
                    <Badge tone={OUTCOME_TONES[r.outcome]}>{r.outcome}</Badge>
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-fg-muted">
                    {pct(r.first_pass.rate)}
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-fg-muted">
                    {pct(r.post_repair.rate)}
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-fg-muted">
                    {pct(r.repair_delta, true)}
                  </td>
                  <td className="px-3 py-1.5 tabular-nums text-fg-muted">{r.repair_iterations}</td>
                  <td className="px-3 py-1.5 tabular-nums text-fg-muted">{r.regressions}</td>
                  <td className="px-3 py-1.5 tabular-nums text-fg-muted">
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
