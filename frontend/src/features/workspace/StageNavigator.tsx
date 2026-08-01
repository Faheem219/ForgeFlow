import { useEffect, useRef, useState } from 'react';

import { Badge } from '../../components/ui';
import type { IntentAction, Stage, StageStatus } from '../../lib/types';
import { STAGE_LABELS, STAGE_ORDER, STATUS_LABELS, STATUS_TONES, prereqUnmet } from './stageMeta';

/**
 * The stage rail — the product's spine (D12: no stage is mandatory, any stage is reachable).
 *
 * Rendered as a connected vertical track rather than six independent cards, because the thing a
 * user needs to read here is a *sequence with an escape hatch*, not a list. A card list implies six
 * unrelated things; a track with a live node implies "this is one pipeline, and you are here — but
 * you may jump."
 */

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

/** Node glyph per status — shape carries meaning, so it survives a colour-blind reading. */
function StageNode({ status, active }: { status: StageStatus; active: boolean }): JSX.Element {
  const base = 'relative z-10 grid h-4 w-4 shrink-0 place-items-center rounded-full border';
  if (status === 'complete') {
    return (
      <span className={`${base} border-success bg-success text-white`}>
        <svg viewBox="0 0 10 10" className="h-2.5 w-2.5" aria-hidden>
          <path
            d="M2 5.2l2 2L8 3"
            stroke="currentColor"
            strokeWidth="1.6"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
    );
  }
  if (status === 'in_progress') {
    return (
      <span className={`${base} border-brand bg-surface`}>
        <span className="motion-essential h-1.5 w-1.5 animate-pulse-dot rounded-full bg-brand" />
      </span>
    );
  }
  if (status === 'awaiting_user' || status === 'stale') {
    return (
      <span className={`${base} border-warning bg-surface`}>
        <span className="h-1.5 w-1.5 rounded-full bg-warning" />
      </span>
    );
  }
  if (status === 'skipped') {
    return (
      <span className={`${base} border-line bg-surface`}>
        <span className="h-px w-2 bg-faint" />
      </span>
    );
  }
  return (
    <span className={`${base} ${active ? 'border-brand' : 'border-line'} bg-surface`}>
      <span className={`h-1 w-1 rounded-full ${active ? 'bg-brand' : 'bg-faint'}`} />
    </span>
  );
}

function StageRow({
  stage,
  status,
  active,
  lockedBy,
  isLast,
  onSelect,
  onAction,
  busy,
}: {
  stage: Stage;
  status: StageStatus;
  active: boolean;
  lockedBy: Stage | null;
  isLast: boolean;
  onSelect: (stage: Stage) => void;
  onAction: (stage: Stage, action: IntentAction) => void;
  busy?: boolean;
}): JSX.Element {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLLIElement | null>(null);

  // Click-away / Escape close. A menu you can only dismiss by re-clicking its trigger is a trap.
  useEffect(() => {
    if (!menuOpen) return;
    function onDown(event: MouseEvent): void {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === 'Escape') setMenuOpen(false);
    }
    document.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  return (
    <li className="relative" ref={menuRef}>
      <div
        className={`group flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors duration-fast ease-out ${
          active ? 'bg-brand-bg' : 'hover:bg-elevated'
        }`}
      >
        <span className="relative flex flex-col items-center self-stretch">
          <StageNode status={status} active={active} />
          {/* The connector to the next node — what makes this a track and not a list. */}
          {!isLast ? (
            <span
              aria-hidden
              className={`absolute left-1/2 top-4 h-[calc(100%+0.375rem)] w-px -translate-x-1/2 ${
                status === 'complete' ? 'bg-success/40' : 'bg-subtle'
              }`}
            />
          ) : null}
        </span>

        <button
          type="button"
          data-testid={`stage-${stage}`}
          onClick={() => onSelect(stage)}
          className="flex min-w-0 flex-1 items-center justify-between gap-2 text-left"
        >
          <span
            className={`flex min-w-0 items-center gap-1.5 truncate text-sm ${
              active ? 'font-medium text-primary' : 'text-secondary'
            }`}
          >
            {lockedBy ? (
              <svg
                viewBox="0 0 12 12"
                className="h-3 w-3 shrink-0 text-faint"
                aria-label="locked"
                role="img"
              >
                <rect x="2.5" y="5.5" width="7" height="5" rx="1" fill="currentColor" />
                <path
                  d="M4.25 5.5V4a1.75 1.75 0 1 1 3.5 0v1.5"
                  stroke="currentColor"
                  strokeWidth="1"
                  fill="none"
                />
              </svg>
            ) : null}
            {STAGE_LABELS[stage]}
          </span>
          <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
        </button>

        <button
          type="button"
          aria-label={`Actions for ${STAGE_LABELS[stage]}`}
          aria-expanded={menuOpen}
          data-testid={`stage-menu-${stage}`}
          onClick={() => setMenuOpen((v) => !v)}
          className="rounded p-0.5 text-faint opacity-0 transition-opacity duration-fast ease-out hover:bg-elevated hover:text-primary focus-visible:opacity-100 group-hover:opacity-100"
        >
          <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" aria-hidden>
            <circle cx="3" cy="8" r="1.25" fill="currentColor" />
            <circle cx="8" cy="8" r="1.25" fill="currentColor" />
            <circle cx="13" cy="8" r="1.25" fill="currentColor" />
          </svg>
        </button>
      </div>

      {lockedBy ? (
        <p className="pb-1 pl-9 text-2xs text-warning">
          Locked until {STAGE_LABELS[lockedBy]} is complete
        </p>
      ) : null}

      {menuOpen ? (
        <div
          role="menu"
          className="absolute right-0 z-20 mt-0.5 w-32 animate-scale-in rounded-md border border-line bg-overlay p-1 shadow-overlay"
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
              className="block w-full rounded px-2 py-1 text-left text-xs text-secondary transition-colors duration-instant ease-out hover:bg-elevated hover:text-primary disabled:opacity-50"
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
      <ul className="space-y-0.5">
        {STAGE_ORDER.map((stage, i) => (
          <StageRow
            key={stage}
            stage={stage}
            status={statusByStage[stage] ?? 'empty'}
            active={stage === activeStage}
            lockedBy={prereqUnmet(stage, statusByStage)}
            isLast={i === STAGE_ORDER.length - 1}
            onSelect={onSelect}
            onAction={onAction}
            busy={busy}
          />
        ))}
      </ul>
    </nav>
  );
}
