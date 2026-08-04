import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Badge, Button } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import type { IntentRequest, TestRunDto } from '../../lib/types';
import { listStages, submitIntent } from '../workspace/api';
import { STATUS_LABELS, STATUS_TONES } from '../workspace/stageMeta';
import { Escalation } from './Escalation';
import { RepairView } from './RepairView';
import { ResultsPanel } from './ResultsPanel';
import { cancelRepair, getLatestRepair, listTestRuns, runRepair } from './api';

/**
 * The Test stage: results on the left, the self-healing loop on the right. This is where the
 * project's contribution becomes visible — the failing count shrinking, the diffs it tried, and
 * the escalation when it knows it is stuck.
 */
export function TestingPanel({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [run, setRun] = useState<TestRunDto | null>(null);

  const stagesQuery = useQuery({
    queryKey: ['stages', projectId],
    queryFn: () => listStages(projectId),
  });
  const status = stagesQuery.data?.find((s) => s.stage === 'test')?.status ?? 'empty';

  const runsQuery = useQuery({
    queryKey: ['test-runs', projectId],
    queryFn: () => listTestRuns(projectId),
  });
  const repairQuery = useQuery({
    queryKey: ['repair-latest', projectId],
    queryFn: () => getLatestRepair(projectId),
  });

  const latestRun = run ?? runsQuery.data?.[0] ?? null;
  const hasFailures = Boolean(latestRun && !latestRun.green && latestRun.failed > 0);

  const repair = useMutation({
    mutationFn: () => runRepair(projectId, latestRun?.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['repair-latest', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['test-runs', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      setRun(null); // fall back to the newest run the loop produced
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Repair failed';
      toast({ title: 'Repair failed', description, variant: 'error' });
    },
  });

  const cancel = useMutation({ mutationFn: () => cancelRepair(projectId) });

  const intent = useMutation({
    mutationFn: (body: IntentRequest) => submitIntent(projectId, body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Action failed';
      toast({ title: 'Stage action rejected', description, variant: 'error' });
    },
  });

  const report = repairQuery.data ?? null;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          Test &amp; repair
          <span data-testid="test-stage-status">
            <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
          </span>
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={intent.isPending}
            data-testid="test-skip"
            onClick={() => intent.mutate({ stage: 'test', action: 'skip' })}
          >
            Skip
          </Button>
          <Button
            size="sm"
            disabled={intent.isPending}
            data-testid="test-proceed"
            onClick={() => intent.mutate({ stage: 'test', action: 'proceed' })}
          >
            Proceed
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-2">
        <div className="min-h-0 overflow-auto">
          <ResultsPanel projectId={projectId} run={latestRun} onRunChange={setRun} />
        </div>

        <div className="min-h-0 space-y-3 overflow-auto">
          <RepairView
            projectId={projectId}
            report={report}
            running={repair.isPending}
            canRepair={hasFailures}
            onRepair={() => repair.mutate()}
            onCancel={() => cancel.mutate()}
          />
          {report?.escalation ? (
            <Escalation projectId={projectId} escalation={report.escalation} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default TestingPanel;
