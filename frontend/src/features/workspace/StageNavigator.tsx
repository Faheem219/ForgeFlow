import { AnimatePresence, motion } from 'framer-motion';
import { Lock, MoreHorizontal, Play, RotateCcw, SkipForward, Wand2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { Badge } from '../../components/ui';
import { cn } from '../../lib/cn';
import { DURATION, EASE } from '../../lib/motion';
import type { IntentAction, Stage, StageStatus } from '../../lib/types';
import {
  STAGE_ICONS,
  STAGE_LABELS,
  STAGE_ORDER,
  STATUS_LABELS,
  STATUS_TONES,
  prereqUnmet,
} from './stageMeta';

interface StageNavigatorProps {
  statusByStage: Partial<Record<Stage, StageStatus>>;
  activeStage: Stage;
  onSelect: (stage: Stage) => void;
  onAction: (stage: Stage, action: IntentAction) => void;
  busy?: boolean;
}

interface StageAction {
  action: IntentAction;
  label: string;
  icon: LucideIcon;
}

const REFINE: StageAction = { action: 'refine', label: 'Refine', icon: Wand2 };
const PROCEED: StageAction = { action: 'proceed', label: 'Proceed', icon: Play };
const SKIP: StageAction = { action: 'skip', label: 'Skip', icon: SkipForward };
const UNSKIP: StageAction = { action: 'unskip', label: 'Un-skip', icon: RotateCcw };

/**
 * The menu for one stage. A skipped stage offers `Un-skip` in place of `Skip`: skipping is
 * reversible, and the server restores the status the stage held (so an accidentally-skipped,
 * already-complete build comes back complete rather than needing a rebuild).
 */
function actionsFor(status: StageStatus): StageAction[] {
  return status === 'skipped' ? [REFINE, PROCEED, UNSKIP] : [REFINE, PROCEED, SKIP];
}

/** Status → tint for the stage's icon well, so progress reads down the rail at a glance. */
const WELL_TINTS: Record<StageStatus, string> = {
  empty: 'border-edge text-fg-subtle',
  in_progress: 'border-brand/50 text-brand-text',
  awaiting_user: 'border-warning/50 text-warning',
  complete: 'border-success/40 text-success',
  skipped: 'border-edge text-fg-faint',
  stale: 'border-warning/40 text-warning',
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
  const rowRef = useRef<HTMLLIElement | null>(null);
  const Icon = STAGE_ICONS[stage];

  // The action menu dismisses like a real menu: click anywhere else, or Escape.
  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(event: PointerEvent): void {
      if (!rowRef.current?.contains(event.target as Node)) setMenuOpen(false);
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === 'Escape') setMenuOpen(false);
    }
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [menuOpen]);

  return (
    <li ref={rowRef} className="group relative">
      <div
        className={cn(
          'flex items-start gap-2.5 rounded-lg px-2 py-2 transition-colors',
          active ? 'bg-brand/10 ring-1 ring-inset ring-brand/25' : 'hover:bg-surface-raised',
        )}
      >
        {/* Name and status stack rather than compete for one line — at rail width the pair does not
            fit side by side, and truncating either one is worse than a second row. */}
        <button
          type="button"
          data-testid={`stage-${stage}`}
          onClick={() => onSelect(stage)}
          className="flex min-w-0 flex-1 items-start gap-2.5 text-left"
        >
          <span
            className={cn(
              'relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border bg-surface transition-colors',
              WELL_TINTS[status],
            )}
          >
            <Icon aria-hidden className="h-4 w-4" strokeWidth={1.75} />
          </span>
          <span className="flex min-w-0 flex-col items-start gap-1 pt-0.5">
            <span className="flex min-w-0 max-w-full items-center gap-1.5 text-sm text-fg">
              {lockedBy ? (
                <Lock
                  aria-label="locked"
                  className="h-3 w-3 shrink-0 text-fg-faint"
                  strokeWidth={2}
                />
              ) : null}
              <span className="truncate font-medium">{STAGE_LABELS[stage]}</span>
            </span>
            <Badge tone={STATUS_TONES[status]}>{STATUS_LABELS[status]}</Badge>
          </span>
        </button>
        <button
          type="button"
          aria-label={`Actions for ${STAGE_LABELS[stage]}`}
          aria-expanded={menuOpen}
          data-testid={`stage-menu-${stage}`}
          onClick={() => setMenuOpen((v) => !v)}
          className="rounded-md p-1 text-fg-faint transition-colors hover:bg-edge-strong/60 hover:text-fg"
        >
          <MoreHorizontal aria-hidden className="h-4 w-4" />
        </button>
      </div>

      {lockedBy ? (
        <p className="px-2 pt-1 text-xs text-warning">
          Locked until {STAGE_LABELS[lockedBy]} is complete
        </p>
      ) : null}

      <AnimatePresence>
        {menuOpen ? (
          <motion.div
            role="menu"
            initial={{ opacity: 0, scale: 0.95, y: -4 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -2 }}
            transition={{ duration: DURATION.fast, ease: EASE }}
            className="absolute right-0 z-10 mt-1 w-36 origin-top-right rounded-lg border border-edge-strong bg-surface-overlay p-1 shadow-xl"
          >
            {actionsFor(status).map(({ action, label, icon: ActionIcon }) => (
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
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-fg transition-colors hover:bg-surface-raised disabled:opacity-50"
              >
                <ActionIcon aria-hidden className="h-3.5 w-3.5 text-fg-subtle" />
                {label}
              </button>
            ))}
          </motion.div>
        ) : null}
      </AnimatePresence>
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
      <div className="relative">
        {/* The rail: a hairline behind the icon wells that turns the list into a pipeline. */}
        <span aria-hidden className="absolute bottom-6 left-6 top-6 w-px bg-edge" />
        <ul className="relative space-y-1">
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
      </div>
    </nav>
  );
}
