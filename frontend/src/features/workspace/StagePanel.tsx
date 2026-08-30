import { Suspense, lazy } from 'react';

import { Badge, Button, EmptyState, Panel, Spinner } from '../../components/ui';
import { formatDateTimeIST } from '../../lib/datetime';
import type { IntentAction, Stage } from '../../lib/types';
import { STAGE_LABELS, STATUS_LABELS, STATUS_TONES } from './stageMeta';
import { useStage } from './useStage';

// The design + build + deploy panels are heavy (Monaco / xterm / React Flow) — only pull them in
// when their stage shows.
const DesignPanel = lazy(() => import('../design/DesignPanel'));
const BuildPanel = lazy(() => import('../build/BuildPanel'));
const RequirementsPanel = lazy(() => import('../requirements/RequirementsPanel'));
const TestingPanel = lazy(() => import('../testing/TestingPanel'));
const DeployPanel = lazy(() => import('../deploy/DeployPanel'));
const Validate = lazy(() => import('../deploy/Validate'));

const STAGE_PANELS = {
  design: DesignPanel,
  build: BuildPanel,
  requirements: RequirementsPanel,
  test: TestingPanel,
  deploy: DeployPanel,
  validate: Validate,
} as const;

const ACTION_LABELS: Record<IntentAction, string> = {
  refine: 'Refine',
  proceed: 'Proceed',
  skip: 'Skip',
  unskip: 'Un-skip',
};

/**
 * Per-stage panel. Design hosts the full design UI; Build hosts the web IDE; Requirements hosts the
 * guided capture form; Test hosts the repair loop; Deploy hosts the topology map. The remaining
 * stages fill in during later epics — for those this scaffold just exposes refine/skip/proceed + an
 * artifacts area.
 */
export function StagePanel({ projectId, stage }: { projectId: string; stage: Stage }): JSX.Element {
  const { stageState, artifacts, runAction, isActing } = useStage(projectId, stage);
  const status = stageState?.status ?? 'empty';

  const ActivePanel = STAGE_PANELS[stage as keyof typeof STAGE_PANELS];
  if (ActivePanel) {
    return (
      <Suspense
        fallback={
          <div className="flex h-full items-center justify-center">
            <Spinner />
          </div>
        }
      >
        <ActivePanel projectId={projectId} />
      </Suspense>
    );
  }

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {STAGE_LABELS[stage]}
          <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
        </span>
      }
      actions={
        <div className="flex gap-2">
          {(['refine', 'proceed', status === 'skipped' ? 'unskip' : 'skip'] as IntentAction[]).map(
            (action) => (
              <Button
                key={action}
                size="sm"
                variant={action === 'proceed' ? 'primary' : 'secondary'}
                disabled={isActing}
                data-testid={`panel-action-${action}`}
                onClick={() => runAction(action)}
              >
                {ACTION_LABELS[action]}
              </Button>
            ),
          )}
        </div>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-fg-muted">
          The {STAGE_LABELS[stage].toLowerCase()} workspace lands in a later phase. For now you can
          drive the stub conductor with the actions above or the chat panel.
        </p>

        <div>
          <h4 className="mb-2 text-xs uppercase tracking-wide text-fg-subtle">Artifacts</h4>
          {artifacts.length === 0 ? (
            <EmptyState title="No artifacts yet" description="Produced outputs will appear here." />
          ) : (
            <ul className="space-y-1" data-testid="artifact-list">
              {artifacts.map((a) => (
                <li
                  key={a.id}
                  className="flex items-center justify-between rounded-lg border border-edge px-3 py-2 text-sm"
                >
                  <span className="text-fg">
                    {a.type} <span className="text-fg-subtle">v{a.version}</span>
                  </span>
                  <span className="text-xs text-fg-subtle">{formatDateTimeIST(a.created_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}
