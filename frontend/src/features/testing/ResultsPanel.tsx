import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronRight, CircleCheck, CircleMinus, CircleX, FlaskConical, Play } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Badge, Button, EmptyState, Spinner, fieldClasses } from '../../components/ui';
import { cn } from '../../lib/cn';
import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import type { TestResultDto, TestRunDto, TestRunSummaryDto, TestScope } from '../../lib/types';
import { getTestRun, getTestStdout, listTestRuns, runTests } from './api';

const STATUS_TONES = {
  passed: 'success',
  failed: 'danger',
  skipped: 'neutral',
} as const;

const SCOPES: TestScope[] = ['all', 'unit', 'e2e'];

const STATUS_ICONS = {
  passed: { icon: CircleCheck, tone: 'text-success' },
  failed: { icon: CircleX, tone: 'text-danger' },
  skipped: { icon: CircleMinus, tone: 'text-fg-faint' },
} as const;

/** Group results by their source file so a failure reads in the context of its suite. */
function groupByFile(results: TestResultDto[]): Array<[string, TestResultDto[]]> {
  const groups = new Map<string, TestResultDto[]>();
  for (const result of results) {
    const key = result.file ?? '(unknown file)';
    const bucket = groups.get(key);
    if (bucket) bucket.push(result);
    else groups.set(key, [result]);
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function FailureDetail({ result }: { result: TestResultDto }): JSX.Element | null {
  if (!result.failure) return null;
  return (
    <div className="mb-1.5 ml-7 mt-1 space-y-1.5 rounded-lg border border-danger/20 bg-danger/[0.06] p-2.5 text-xs">
      <p className="font-medium text-danger">{result.failure.message}</p>
      {result.failure.stack ? (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-surface-sunken/60 p-2 font-mono text-2xs leading-relaxed text-fg-muted">
          {result.failure.stack}
        </pre>
      ) : null}
      {result.failure.files_referenced.length > 0 ? (
        <p className="text-fg-subtle">
          Implicated:{' '}
          <span className="font-mono">{result.failure.files_referenced.join(', ')}</span>
        </p>
      ) : null}
    </div>
  );
}

export function ResultsPanel({
  projectId,
  run,
  onRunChange,
}: {
  projectId: string;
  run: TestRunSummaryDto | null;
  onRunChange?: (run: TestRunDto) => void;
}): JSX.Element {
  const queryClient = useQueryClient();
  const [scope, setScope] = useState<TestScope>('all');
  const [filter, setFilter] = useState('');
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showStdout, setShowStdout] = useState(false);

  const runsQuery = useQuery({
    queryKey: ['test-runs', projectId],
    queryFn: () => listTestRuns(projectId),
  });
  const latest = run ?? runsQuery.data?.[0] ?? null;

  // `listTestRuns` returns summaries — no per-test results — so the selected run is fetched in
  // full. Without this a reload would show a run's counts above an empty result list. A fresh run
  // seeds this cache key below, so running the suites costs no extra request.
  const detailQuery = useQuery({
    queryKey: ['test-run', projectId, latest?.id],
    queryFn: () => getTestRun(projectId, latest?.id as string),
    enabled: Boolean(latest?.id),
  });

  const stdoutQuery = useQuery({
    queryKey: ['test-stdout', latest?.id],
    queryFn: () => getTestStdout(projectId, latest?.id as string),
    enabled: showStdout && Boolean(latest?.id),
  });

  const execute = useMutation({
    mutationFn: () => runTests(projectId, scope, filter.trim() || undefined),
    onSuccess: (fresh) => {
      onRunChange?.(fresh);
      queryClient.setQueryData(['test-run', projectId, fresh.id], fresh);
      void queryClient.invalidateQueries({ queryKey: ['test-runs', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Test run failed';
      toast({ title: 'Could not run tests', description, variant: 'error' });
    },
  });

  const detail = detailQuery.data ?? null;
  const grouped = useMemo(() => groupByFile(detail?.results ?? []), [detail]);

  return (
    <div className="flex min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-[13px] font-semibold text-fg">
          <FlaskConical aria-hidden className="h-3.5 w-3.5 text-fg-subtle" strokeWidth={1.75} />
          Tests
          {latest ? (
            <span data-testid="test-summary">
              <Badge tone={latest.green ? 'success' : 'danger'}>
                {latest.passed}/{latest.total} passing
              </Badge>
            </span>
          ) : null}
        </span>
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="Test scope"
            data-testid="test-scope"
            value={scope}
            onChange={(e) => setScope(e.target.value as TestScope)}
            disabled={execute.isPending}
            className={cn(fieldClasses, 'h-7 w-auto py-0 text-xs')}
          >
            {SCOPES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <input
            aria-label="Test name filter"
            data-testid="test-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="filter…"
            disabled={execute.isPending}
            className={cn(fieldClasses, 'h-7 w-28 py-0 text-xs')}
          />
          <Button
            size="sm"
            variant="secondary"
            disabled={execute.isPending}
            data-testid="run-tests"
            onClick={() => execute.mutate()}
          >
            {execute.isPending ? null : <Play aria-hidden className="h-3 w-3" strokeWidth={2} />}
            {execute.isPending ? 'Running…' : 'Run tests'}
          </Button>
        </div>
      </header>

      {execute.isPending ? (
        <div className="flex items-center gap-2 text-xs text-fg-muted" data-testid="tests-running">
          <span className="text-brand-text">
            <Spinner size="sm" />
          </span>
          Running the suites in the sandbox…
        </div>
      ) : null}

      {!latest ? (
        <EmptyState title="No test runs yet" description="Run the suites to see results here." />
      ) : (
        <div className="min-h-0 flex-1 space-y-3 overflow-auto" data-testid="test-results">
          {grouped.map(([file, results]) => (
            <section key={file}>
              <h4 className="mb-1 truncate px-1 font-mono text-2xs text-fg-faint" title={file}>
                {file}
              </h4>
              <ul className="overflow-hidden rounded-lg border border-edge">
                {results.map((result) => {
                  const id = `${file}::${result.name}`;
                  const open = expanded === id;
                  const { icon: StatusIcon, tone } = STATUS_ICONS[result.status];
                  return (
                    <li key={id} className="border-b border-edge last:border-b-0">
                      <button
                        type="button"
                        data-testid={`test-${result.status}`}
                        aria-expanded={result.failure ? open : undefined}
                        onClick={() => setExpanded(open ? null : id)}
                        className="flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left text-[13px] transition-colors hover:bg-fg/[0.03]"
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <StatusIcon
                            aria-hidden
                            className={cn('h-3.5 w-3.5 shrink-0', tone)}
                            strokeWidth={2}
                          />
                          <span className="truncate text-fg">{result.name}</span>
                          {result.failure ? (
                            <ChevronRight
                              aria-hidden
                              className={cn(
                                'h-3 w-3 shrink-0 text-fg-faint transition-transform duration-150',
                                open && 'rotate-90',
                              )}
                            />
                          ) : null}
                        </span>
                        <span className="flex shrink-0 items-center gap-1.5">
                          {result.criterion_id ? (
                            <span
                              className="font-mono text-2xs text-fg-faint"
                              title="Acceptance criterion"
                            >
                              {result.criterion_id}
                            </span>
                          ) : null}
                          <Badge tone={STATUS_TONES[result.status]}>{result.status}</Badge>
                        </span>
                      </button>
                      {open ? <FailureDetail result={result} /> : null}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}

      {latest ? (
        <div className="space-y-1">
          <Button
            size="sm"
            variant="ghost"
            data-testid="toggle-stdout"
            onClick={() => setShowStdout((open) => !open)}
          >
            {showStdout ? 'Hide output' : 'Show reporter output'}
          </Button>
          {showStdout ? (
            <pre
              data-testid="test-stdout"
              className="max-h-48 overflow-auto rounded-lg border border-edge bg-surface-sunken p-2.5 font-mono text-2xs leading-relaxed text-fg-muted"
            >
              {stdoutQuery.isFetching ? 'Loading…' : (stdoutQuery.data?.stdout ?? '(no output)')}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export default ResultsPanel;
