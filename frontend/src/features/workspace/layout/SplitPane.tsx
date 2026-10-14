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

/**
 * Sizes in this app are *percentages of the group* — a rail is "a fifth of the width", never "180
 * pixels", because the whole point of phase-61 was to stop hard-coding dimensions.
 *
 * The library disagrees about bare numbers: since v4 it reads `minSize={12}` as **12 pixels** and
 * only strings without a unit as percentages. Left unconverted that pins the stage rail to a 34px
 * ceiling and the assistant to 45px — both collapse to a sliver of vertical text and the toggles
 * look broken. Normalise here, once, so no call site has to remember the unit rule.
 */
type PaneSize = number | string;

function pct(size: PaneSize | undefined): string | undefined {
  if (size === undefined) return undefined;
  // Strings pass through: they may carry a deliberate unit ("240px", "20rem").
  return typeof size === 'number' ? `${size}%` : size;
}

interface PaneProps {
  id: string;
  /** Percentage of the group. */
  defaultSize?: PaneSize;
  /** Percentage of the group. */
  minSize?: PaneSize;
  /** Percentage of the group. */
  maxSize?: PaneSize;
  collapsible?: boolean;
  collapsedSize?: PaneSize;
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
      defaultSize={pct(defaultSize)}
      minSize={pct(minSize)}
      maxSize={pct(maxSize)}
      collapsible={collapsible}
      collapsedSize={pct(collapsedSize)}
      className={cn('flex min-h-0 min-w-0 flex-col', className)}
    >
      {children}
    </Panel>
  );
}

/**
 * A draggable seam. The visible line is thin, but the grab target is not — a 1px hit area is the
 * difference between "resizable" and "technically resizable".
 *
 * `gap` (default) sits in the gutter between two framed panes and shows a short grip on hover;
 * `hairline` is the seam *inside* one frame (the IDE's tree/editor split), drawn as the border
 * between them with the hit area extended either side of it.
 */
export function PaneHandle({
  direction,
  variant = 'gap',
}: {
  direction: 'horizontal' | 'vertical';
  variant?: 'gap' | 'hairline';
}) {
  const horizontal = direction === 'horizontal';
  if (variant === 'hairline') {
    return (
      <Separator
        aria-label="Resize panels"
        className={cn(
          'group relative shrink-0 bg-edge transition-colors duration-150 motion-reduce:transition-none',
          'hover:bg-brand/60 active:bg-brand/80 data-[separator=active]:bg-brand/80',
          horizontal
            ? 'w-px cursor-col-resize before:absolute before:inset-y-0 before:-left-1.5 before:-right-1.5 before:content-[""]'
            : 'h-px cursor-row-resize before:absolute before:inset-x-0 before:-bottom-1.5 before:-top-1.5 before:content-[""]',
        )}
      />
    );
  }
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
          'rounded-full bg-transparent transition-colors duration-150 motion-reduce:transition-none',
          // A short delay, so a pointer crossing the gutter on its way somewhere does not flash it.
          'group-hover:bg-fg-faint group-hover:delay-100 group-active:bg-brand-text group-active:delay-0',
          horizontal ? 'h-10 w-[3px]' : 'h-[3px] w-10',
        )}
      />
    </Separator>
  );
}
