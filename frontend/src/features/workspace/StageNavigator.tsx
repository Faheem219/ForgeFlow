import { useState } from 'react';

import { Badge } from '../../components/ui';
import type { IntentAction, Stage, StageStatus } from '../../lib/types';
import { STAGE_LABELS, STAGE_ORDER, STATUS_LABELS, STATUS_TONES, prereqUnmet } from './stageMeta';

interface StageNavigatorProps {
  statusByStage: Partial<Record<Stage, StageStatus>>;
  activeStage: Stage;
  onSelect: (stage: Stage) => void;
  onAction: (stage: Stage, action: IntentAction) => void;
  busy?: boolean;
}

const ACTIONS: IntentAction[] = ['refine', 'proceed', 'skip'];
const ACTION_LABELS: Record<IntentAction, string> = {
  refine: 'Refine',
  proceed: 'Proceed',
  skip: 'Skip',
};

function StageRow({
  stage,
  status,
  active,
  lockedBy,
  onSelect,
  onAction,
  busy,
}: {
  stage: Stage;
  status: StageStatus;
  active: boolean;
  lockedBy: Stage | null;
  onSelect: (stage: Stage) => void;
  onAction: (stage: Stage, action: IntentAction) => void;
  busy?: boolean;
}): JSX.Element {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <li className="relative">
      <div
        className={`flex items-start gap-2 rounded-lg border px-3 py-2 ${
          active ? 'border-brand bg-brand/10' : 'border-edge hover:bg-surface-raised'
        }`}
      >
        {/* Name and status stack rather than compete for one line — at rail width the pair does not
            fit side by side, and truncating either one is worse than a second row. */}
        <button
          type="button"
          data-testid={`stage-${stage}`}
          onClick={() => onSelect(stage)}
          className="flex min-w-0 flex-1 flex-col items-start gap-1 text-left"
        >
          <span className="flex min-w-0 max-w-full items-center gap-1.5 text-sm text-fg">
            {lockedBy ? (
              <span
                title={`Requires ${STAGE_LABELS[lockedBy]} to be complete`}
                aria-label="locked"
                className="shrink-0 text-[11px]"
              >
                🔒
              </span>
            ) : null}
            <span className="truncate">{STAGE_LABELS[stage]}</span>
          </span>
          <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
        </button>
        <button
          type="button"
          aria-label={`Actions for ${STAGE_LABELS[stage]}`}
          data-testid={`stage-menu-${stage}`}
          onClick={() => setMenuOpen((v) => !v)}
          className="rounded p-1 text-fg-muted hover:bg-edge-strong"
        >
          ⋯
        </button>
      </div>

      {lockedBy ? (
        <p className="px-3 pt-1 text-xs text-warning">
          Locked until {STAGE_LABELS[lockedBy]} is complete
        </p>
      ) : null}

      {menuOpen ? (
        <div
          role="menu"
          className="absolute right-0 z-10 mt-1 w-32 rounded-lg border border-edge-strong bg-surface-overlay p-1 shadow-xl"
        >
          {ACTIONS.map((action) => (
            <button
              key={action}
              type="button"
              role="menuitem"
              disabled={busy}
              data-testid={`stage-action-${stage}-${action}`}
              onClick={() => {
                setMenuOpen(false);
                onAction(stage, action);
              }}
              className="block w-full rounded px-2 py-1.5 text-left text-sm text-fg hover:bg-surface-raised disabled:opacity-50"
            >
              {ACTION_LABELS[action]}
            </button>
          ))}
        </div>
      ) : null}
    </li>
  );
}

export function StageNavigator({
  statusByStage,
  activeStage,
  onSelect,
  onAction,
  busy,
}: StageNavigatorProps): JSX.Element {
  return (
    <nav aria-label="Stages">
      <ul className="space-y-1.5">
        {STAGE_ORDER.map((stage) => (
          <StageRow
            key={stage}
            stage={stage}
            status={statusByStage[stage] ?? 'empty'}
            active={stage === activeStage}
            lockedBy={prereqUnmet(stage, statusByStage)}
            onSelect={onSelect}
            onAction={onAction}
            busy={busy}
          />
        ))}
      </ul>
    </nav>
  );
}
