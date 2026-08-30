import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Badge, Button, EmptyState, Spinner } from '../../components/ui';
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
    <div className="mt-1 space-y-1 rounded-lg border border-danger/30 bg-danger/10 p-2 text-xs">
      <p className="font-medium text-danger">{result.failure.message}</p>
      {result.failure.stack ? (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-fg-muted">
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
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
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
            className="rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1 text-sm text-fg"
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
            className="w-28 rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1 text-sm text-fg"
          />
          <Button
            size="sm"
            disabled={execute.isPending}
            data-testid="run-tests"
            onClick={() => execute.mutate()}
          >
            {execute.isPending ? 'Running…' : 'Run tests'}
          </Button>
        </div>
      </header>

      {execute.isPending ? (
        <div className="flex items-center gap-2 text-xs text-fg-muted" data-testid="tests-running">
          <Spinner /> Running the suites in the sandbox…
        </div>
      ) : null}

      {!latest ? (
        <EmptyState title="No test runs yet" description="Run the suites to see results here." />
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-auto" data-testid="test-results">
          {grouped.map(([file, results]) => (
            <section key={file} className="rounded-xl border border-edge p-2">
              <h4 className="mb-1 font-mono text-xs text-fg-subtle">{file}</h4>
              <ul className="space-y-1">
                {results.map((result) => {
                  const id = `${file}::${result.name}`;
                  const open = expanded === id;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        data-testid={`test-${result.status}`}
                        onClick={() => setExpanded(open ? null : id)}
                        className="flex w-full items-start justify-between gap-2 rounded-lg px-2 py-1 text-left text-sm hover:bg-surface-raised"
                      >
                        <span className="text-fg">{result.name}</span>
                        <span className="flex shrink-0 items-center gap-1">
                          {result.criterion_id ? (
                            <span
                              className="font-mono text-[11px] text-fg-subtle"
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
              className="max-h-48 overflow-auto rounded-lg border border-edge bg-surface-sunken p-2 font-mono text-[11px] text-fg-muted"
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
