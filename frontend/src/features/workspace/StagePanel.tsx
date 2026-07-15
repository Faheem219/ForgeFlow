import { Suspense, lazy } from 'react';

import { Badge, Button, EmptyState, Panel, Spinner } from '../../components/ui';
import type { IntentAction, Stage } from '../../lib/types';
import { STAGE_LABELS, STATUS_LABELS, STATUS_TONES } from './stageMeta';
import { useStage } from './useStage';

// Monaco is heavy — only pull it in when the Build stage is actually shown.
const Ide = lazy(() => import('../ide/Ide'));

const ACTION_LABELS: Record<IntentAction, string> = {
  refine: 'Refine',
  proceed: 'Proceed',
  skip: 'Skip',
};

/**
 * Per-stage panel. Build hosts the web IDE; the remaining stages (design upload, requirements
 * form, test/repair view, deploy topology, data browser) fill in during later epics — for those
 * this scaffold just exposes the refine/skip/proceed affordances and an artifacts area.
 */
export function StagePanel({ projectId, stage }: { projectId: string; stage: Stage }): JSX.Element {
  const { stageState, artifacts, runAction, isActing } = useStage(projectId, stage);
  const status = stageState?.status ?? 'empty';

  if (stage === 'build') {
    return (
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-medium text-slate-200">
            {STAGE_LABELS.build}
            <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
          </span>
          <div className="flex gap-2">
            {(['refine', 'proceed', 'skip'] as IntentAction[]).map((action) => (
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
            ))}
          </div>
        </div>
        <div className="min-h-0 flex-1">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center">
                <Spinner />
              </div>
            }
          >
            <Ide projectId={projectId} />
          </Suspense>
        </div>
      </div>
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
          {(['refine', 'proceed', 'skip'] as IntentAction[]).map((action) => (
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
          ))}
        </div>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-slate-400">
          The {STAGE_LABELS[stage].toLowerCase()} workspace lands in a later phase. For now you can
          drive the stub conductor with the actions above or the chat panel.
        </p>

        <div>
          <h4 className="mb-2 text-xs uppercase tracking-wide text-slate-500">Artifacts</h4>
          {artifacts.length === 0 ? (
            <EmptyState title="No artifacts yet" description="Produced outputs will appear here." />
          ) : (
            <ul className="space-y-1" data-testid="artifact-list">
              {artifacts.map((a) => (
                <li
                  key={a.id}
                  className="flex items-center justify-between rounded-lg border border-slate-800 px-3 py-2 text-sm"
                >
                  <span className="text-slate-200">
                    {a.type} <span className="text-slate-500">v{a.version}</span>
                  </span>
                  <span className="text-xs text-slate-500">
                    {new Date(a.created_at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}
