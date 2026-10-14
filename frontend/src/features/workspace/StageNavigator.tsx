import { AnimatePresence, motion } from 'framer-motion';
import { Lock, MoreHorizontal, Play, RotateCcw, SkipForward, Undo2, Wand2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';

import { cn } from '../../lib/cn';
import { DURATION, EASE } from '../../lib/motion';
import type { IntentAction, Stage, StageStatus } from '../../lib/types';
import {
  STAGE_ICONS,
  STAGE_LABELS,
  STAGE_ORDER,
  STATUS_LABELS,
  canReset,
  prereqUnmet,
  resetHint,
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
const RESET: StageAction = { action: 'reset', label: 'Reset', icon: Undo2 };

/**
 * The menu for one stage. A skipped stage offers `Un-skip` in place of `Skip`: skipping is
 * reversible, and the server restores the status the stage held (so an accidentally-skipped,
 * already-complete build comes back complete rather than needing a rebuild). A finished stage also
 * offers `Reset`, which takes the "done" back without re-running anything.
 */
function actionsFor(stage: Stage, status: StageStatus): StageAction[] {
  const actions = status === 'skipped' ? [REFINE, PROCEED, UNSKIP] : [REFINE, PROCEED, SKIP];
  return canReset(stage, status) ? [...actions, RESET] : actions;
}

/** Status → the node's ring and glyph colour, so progress reads down the rail at a glance. */
const NODE_TINTS: Record<StageStatus, string> = {
  empty: 'border-edge-strong text-fg-faint',
  // A stored status, not a live signal — so a still halo rather than motion. (A stage can sit at
  // "In progress" with nothing running; a spinner there would claim work that is not happening.)
  in_progress: 'border-brand/60 text-brand-text ring-[3px] ring-brand/15',
  awaiting_user: 'border-warning/60 text-warning',
  complete: 'border-success/45 text-success',
  skipped: 'border-dashed border-edge-strong text-fg-faint',
  stale: 'border-warning/45 text-warning',
};

/** Status → the small dot beside the status word. The word carries the meaning; the dot the hue. */
const STATUS_DOTS: Record<StageStatus, string> = {
  empty: 'bg-fg-faint/60',
  in_progress: 'bg-brand-text',
  awaiting_user: 'bg-warning',
  complete: 'bg-success',
  skipped: 'bg-fg-faint/60',
  stale: 'bg-warning',
};

function StageRow({
  stage,
  status,
  active,
  lockedBy,
  /** Whether the rail segment *below* this node reads as travelled (this stage is done). */
  railDone,
  last,
  highlightId,
  onSelect,
  onAction,
  busy,
}: {
  stage: Stage;
  status: StageStatus;
  active: boolean;
  lockedBy: Stage | null;
  railDone: boolean;
  last: boolean;
  highlightId: string;
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
    <li ref={rowRef} className="group/row relative">
      {/* The rail segment from this node down to the next one. Drawn per row so each segment can
          carry its own state: solid where the pipeline has been travelled, hairline where not. */}
      {!last ? (
        <span
          aria-hidden
          className={cn(
            'absolute left-[1.3rem] top-[2.15rem] -bottom-[0.4rem] w-px transition-colors duration-300',
            railDone ? 'bg-success/40' : 'bg-edge',
          )}
        />
      ) : null}

      <div className="relative flex items-start gap-1 rounded-lg">
        {active ? (
          // One highlight glides between rows instead of blinking in per row.
          <motion.span
            layoutId={highlightId}
            aria-hidden
            className="absolute inset-0 rounded-lg bg-fg/[0.065]"
            transition={{ duration: 0.22, ease: EASE }}
          />
        ) : null}

        {/* Name and status stack rather than compete for one line — at rail width the pair does not
            fit side by side, and truncating either one is worse than a second row. */}
        <button
          type="button"
          data-testid={`stage-${stage}`}
          aria-current={active ? 'step' : undefined}
          onClick={() => onSelect(stage)}
          className={cn(
            'relative flex min-w-0 flex-1 items-start gap-2.5 rounded-lg py-2 pl-2 pr-1 text-left transition-colors duration-150',
            !active && 'hover:bg-fg/[0.03]',
          )}
        >
          <span
            className={cn(
              'relative z-10 mt-px flex h-[1.375rem] w-[1.375rem] shrink-0 items-center justify-center rounded-full border bg-surface transition-colors duration-300',
              NODE_TINTS[status],
            )}
          >
            <Icon aria-hidden className="h-3 w-3" strokeWidth={2} />
          </span>
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="flex min-w-0 items-center gap-1.5">
              <span
                className={cn(
                  'truncate text-[13px] leading-5',
                  active ? 'font-medium text-fg' : 'text-fg-muted group-hover/row:text-fg',
                )}
              >
                {STAGE_LABELS[stage]}
              </span>
              {lockedBy ? (
                <Lock
                  aria-label="locked"
                  className="h-3 w-3 shrink-0 text-fg-faint"
                  strokeWidth={2}
                />
              ) : null}
            </span>
            <span className="flex items-center gap-1.5 text-2xs text-fg-subtle">
              <span aria-hidden className={cn('h-1.5 w-1.5 rounded-full', STATUS_DOTS[status])} />
              {STATUS_LABELS[status]}
            </span>
            {lockedBy ? (
              <span className="text-2xs leading-snug text-fg-faint">
                Locked until {STAGE_LABELS[lockedBy]} is complete
              </span>
            ) : null}
          </span>
        </button>
        <button
          type="button"
          aria-label={`Actions for ${STAGE_LABELS[stage]}`}
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          data-testid={`stage-menu-${stage}`}
          onClick={() => setMenuOpen((v) => !v)}
          className={cn(
            'relative mr-1 mt-2 rounded-md p-1 text-fg-faint transition-[opacity,color,background-color] duration-150',
            'hover:bg-fg/[0.08] hover:text-fg focus-visible:opacity-100',
            menuOpen
              ? 'bg-fg/[0.08] text-fg opacity-100'
              : 'opacity-0 group-hover/row:opacity-100 [@media(hover:none)]:opacity-100',
          )}
        >
          <MoreHorizontal aria-hidden className="h-3.5 w-3.5" />
        </button>
      </div>

      <AnimatePresence>
        {menuOpen ? (
          <motion.div
            role="menu"
            aria-label={`${STAGE_LABELS[stage]} actions`}
            initial={{ opacity: 0, scale: 0.96, y: -4 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: -2 }}
            transition={{ duration: DURATION.fast, ease: EASE }}
            className="absolute right-1 top-9 z-20 w-40 origin-top-right rounded-lg bg-surface-overlay p-1 shadow-pop"
          >
            {actionsFor(stage, status).map(({ action, label, icon: ActionIcon }) => (
              <button
                key={action}
                type="button"
                role="menuitem"
                disabled={busy}
                title={action === 'reset' ? resetHint(stage) : undefined}
                data-testid={`stage-action-${stage}-${action}`}
                onClick={() => {
                  setMenuOpen(false);
                  onAction(stage, action);
                }}
                className="flex h-8 w-full items-center gap-2 rounded-md px-2 text-left text-[13px] text-fg transition-colors hover:bg-surface-raised disabled:opacity-50"
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
  // Scoped per navigator, so two mounted rails could never steal each other's highlight.
  const highlightId = `stage-active-${useId()}`;
  return (
    <nav aria-label="Stages">
      <ul className="relative space-y-0.5">
        {STAGE_ORDER.map((stage, i) => {
          const status = statusByStage[stage] ?? 'empty';
          return (
            <StageRow
              key={stage}
              stage={stage}
              status={status}
              active={stage === activeStage}
              lockedBy={prereqUnmet(stage, statusByStage)}
              railDone={status === 'complete' || status === 'skipped'}
              last={i === STAGE_ORDER.length - 1}
              highlightId={highlightId}
              onSelect={onSelect}
              onAction={onAction}
              busy={busy}
            />
          );
        })}
      </ul>
    </nav>
  );
}
