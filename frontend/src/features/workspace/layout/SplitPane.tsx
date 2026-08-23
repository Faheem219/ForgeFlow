import { Group, Panel, Separator, useDefaultLayout } from 'react-resizable-panels';
import type { ReactNode } from 'react';

import { cn } from '../../../lib/cn';
import { safeStorage } from './layoutStorage';

/**
 * Styled wrappers around `react-resizable-panels` (phase-61).
 *
 * The workspace used to be three fixed-width columns plus a fixed-height terminal, so the centre
 * column was the only elastic region — and then it subdivided again for the IDE and the preview.
 * With both open that left each around 200px, which is the reported "neither is properly visible".
 * No amount of density work fixes that; the fixed dimensions have to become negotiable.
 *
 * These wrappers exist so the seams read as part of the design (semantic tokens, a real hit area,
 * a visible hover state) rather than as library chrome, and so every split in the app persists and
 * behaves the same way.
 */

interface SplitProps {
  /** Unique per project + location; drives persistence. */
  id: string;
  direction: 'horizontal' | 'vertical';
  /** Panel ids rendered on mount — required for groups with conditional panels. */
  panelIds: string[];
  className?: string;
  children: ReactNode;
}

/** A persisted, resizable group. Sizes survive a reload and are scoped per project. */
export function Split({ id, direction, panelIds, className, children }: SplitProps) {
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({
    id,
    panelIds,
    // Only user drags are saved; a window resize or an imperative collapse must not overwrite the
    // arrangement someone deliberately chose.
    onlySaveAfterUserInteractions: true,
    storage: safeStorage(),
  });

  return (
    <Group
      className={cn('min-h-0 min-w-0', className)}
      orientation={direction}
      defaultLayout={defaultLayout}
      onLayoutChanged={onLayoutChanged}
    >
      {children}
    </Group>
  );
}

interface PaneProps {
  id: string;
  /** Percentage of the group. */
  defaultSize?: number;
  minSize?: number;
  maxSize?: number;
  collapsible?: boolean;
  collapsedSize?: number;
  className?: string;
  children: ReactNode;
}

/** One pane. `minSize` is an honest floor — panes stop shrinking rather than silently squeezing. */
export function Pane({
  id,
  defaultSize,
  minSize,
  maxSize,
  collapsible,
  collapsedSize,
  className,
  children,
}: PaneProps) {
  return (
    <Panel
      id={id}
      defaultSize={defaultSize}
      minSize={minSize}
      maxSize={maxSize}
      collapsible={collapsible}
      collapsedSize={collapsedSize}
      className={cn('flex min-h-0 min-w-0 flex-col', className)}
    >
      {children}
    </Panel>
  );
}

/**
 * A draggable seam. The visible line is thin, but the grab target is not — a 1px hit area is the
 * difference between "resizable" and "technically resizable".
 */
export function PaneHandle({ direction }: { direction: 'horizontal' | 'vertical' }) {
  const horizontal = direction === 'horizontal';
  return (
    <Separator
      aria-label="Resize panels"
      className={cn(
        'group relative flex shrink-0 items-center justify-center',
        'transition-colors duration-150 motion-reduce:transition-none',
        horizontal ? 'w-2 cursor-col-resize' : 'h-2 cursor-row-resize',
      )}
    >
      <span
        aria-hidden
        className={cn(
          'rounded-full bg-edge transition-colors duration-150 motion-reduce:transition-none',
          'group-hover:bg-accent group-data-[separator]:group-active:bg-accent',
          horizontal ? 'h-8 w-0.5' : 'h-0.5 w-8',
        )}
      />
    </Separator>
  );
}
