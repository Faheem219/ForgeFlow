import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';

import { PANEL_DEFAULTS, useUiStore, usePanelSize } from '../../lib/stores/uiStore';

/**
 * Draggable splitters.
 *
 * Hand-rolled rather than pulled from a library because the frontend stack is fixed
 * (IMPLEMENTATION_PLAN.md §4) — and because the behaviour that actually matters here is small:
 *
 *  - **Pointer capture**, so a fast drag that leaves the handle (or the window) keeps tracking.
 *    This is the single thing most naive splitters get wrong; without it the panel "sticks".
 *  - **No re-render per frame.** The dragged size is written straight to the DOM node's inline
 *    style and only committed to the store on release. Dragging a splitter that re-renders a Monaco
 *    editor 60×/second is why resizable panels usually feel awful.
 *  - **Keyboard resize**, because a splitter is a real control: arrows nudge, Home/End snap,
 *    double-click restores the default.
 *
 * Sizes are px (not %) so a panel keeps its useful width when the window changes — a file tree that
 * shrinks to nothing on a narrow window is worse than one that holds its ground and scrolls.
 */

export type Orientation = 'vertical' | 'horizontal';

export interface ResizablePanelProps {
  /** Persisted size key — see `PanelKey` in uiStore. */
  panelKey: string;
  /** `vertical` = a vertical seam, resizing WIDTH. `horizontal` = a horizontal seam, resizing HEIGHT. */
  orientation?: Orientation;
  /** Which side of the splitter this panel sits on (decides drag direction). */
  side?: 'start' | 'end';
  min?: number;
  max?: number;
  className?: string;
  children: ReactNode;
  /** Accessible name for the handle, e.g. "Resize file tree". */
  label: string;
}

const KEYBOARD_STEP = 16;
const KEYBOARD_STEP_LARGE = 64;

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/**
 * A fixed-size pane plus the splitter that resizes it. Render it as a flex child next to the
 * flexible pane; the splitter is emitted on the `side` that faces its neighbour.
 */
export function ResizablePanel({
  panelKey,
  orientation = 'vertical',
  side = 'start',
  min = 160,
  max = 720,
  className = '',
  children,
  label,
}: ResizablePanelProps): JSX.Element {
  const stored = usePanelSize(panelKey);
  const setPanelSize = useUiStore((s) => s.setPanelSize);

  const paneRef = useRef<HTMLDivElement | null>(null);
  const [dragging, setDragging] = useState(false);
  // Live size during a drag. Kept in a ref (not state) so a drag never re-renders the subtree.
  const liveSize = useRef(stored);
  const dragStart = useRef({ pointer: 0, size: 0 });
  const handleId = useId();

  const isVertical = orientation === 'vertical';
  const axisProp = isVertical ? 'width' : 'height';

  // Follow store changes that did not come from this drag (reset, another tab, rehydrate).
  useEffect(() => {
    if (dragging) return;
    liveSize.current = stored;
    const pane = paneRef.current;
    if (pane) pane.style[axisProp] = `${stored}px`;
  }, [stored, dragging, axisProp]);

  const applySize = useCallback(
    (next: number): number => {
      const size = clamp(next, min, max);
      liveSize.current = size;
      const pane = paneRef.current;
      if (pane) pane.style[axisProp] = `${size}px`;
      return size;
    },
    [min, max, axisProp],
  );

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      // Ignore secondary buttons; a right-click drag is not a resize.
      if (event.button !== 0) return;
      event.preventDefault();
      // Capture makes the handle keep receiving moves even when the pointer outruns it.
      event.currentTarget.setPointerCapture(event.pointerId);
      dragStart.current = {
        pointer: isVertical ? event.clientX : event.clientY,
        size: liveSize.current,
      };
      setDragging(true);
      document.body.classList.add('dragging-none');
    },
    [isVertical],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      const pointer = isVertical ? event.clientX : event.clientY;
      const delta = pointer - dragStart.current.pointer;
      // A panel on the `end` side grows as the pointer moves *toward* the start.
      applySize(dragStart.current.size + (side === 'start' ? delta : -delta));
    },
    [dragging, isVertical, side, applySize],
  );

  const endDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (!dragging) return;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      setDragging(false);
      document.body.classList.remove('dragging-none');
      // Commit once, on release — not on every frame.
      setPanelSize(panelKey, liveSize.current);
    },
    [dragging, panelKey, setPanelSize],
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const grow = isVertical ? 'ArrowRight' : 'ArrowDown';
      const shrink = isVertical ? 'ArrowLeft' : 'ArrowUp';
      const step = event.shiftKey ? KEYBOARD_STEP_LARGE : KEYBOARD_STEP;
      const direction = side === 'start' ? 1 : -1;

      let next: number | null = null;
      if (event.key === grow) next = liveSize.current + step * direction;
      else if (event.key === shrink) next = liveSize.current - step * direction;
      else if (event.key === 'Home') next = min;
      else if (event.key === 'End') next = max;

      if (next === null) return;
      event.preventDefault();
      setPanelSize(panelKey, applySize(next));
    },
    [isVertical, side, min, max, panelKey, applySize, setPanelSize],
  );

  const restoreDefault = useCallback(() => {
    setPanelSize(panelKey, applySize(PANEL_DEFAULTS[panelKey] ?? 280));
  }, [panelKey, applySize, setPanelSize]);

  // Belt-and-braces: if the component unmounts mid-drag the body class must not survive it.
  useEffect(() => () => document.body.classList.remove('dragging-none'), []);

  const paneStyle: CSSProperties = { [axisProp]: `${stored}px` };
  const splitter = (
    <div
      role="separator"
      id={handleId}
      tabIndex={0}
      aria-label={label}
      aria-orientation={isVertical ? 'vertical' : 'horizontal'}
      aria-valuenow={Math.round(stored)}
      aria-valuemin={min}
      aria-valuemax={max}
      data-orientation={orientation}
      data-dragging={dragging}
      data-testid={`splitter-${panelKey}`}
      className="splitter"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onKeyDown={onKeyDown}
      onDoubleClick={restoreDefault}
    />
  );

  return (
    <>
      {side === 'end' ? splitter : null}
      <div
        ref={paneRef}
        style={paneStyle}
        className={`flex min-h-0 min-w-0 shrink-0 flex-col ${className}`}
      >
        {children}
      </div>
      {side === 'start' ? splitter : null}
    </>
  );
}

export default ResizablePanel;
