import { X } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

import { cn } from '../../../lib/cn';

/**
 * Full-window focus mode for a single pane (phase-61).
 *
 * Resizing serves the everyday case — watch the build, glance at the preview. Focus serves the
 * moment one pane *is* the whole task: reading a stack trace, clicking through the generated app.
 *
 * **The children stay mounted.** Unmounting the underlying pane and re-rendering it elsewhere would
 * be far simpler, and would silently reload the preview iframe and destroy the terminal's
 * scrollback — the two things a user is most likely to be maximizing in order to read.
 *
 * That rules out a portal: moving children to a different portal container remounts them just as
 * surely as re-rendering them would (the first draft did exactly this, and the mount-preservation
 * test caught it). So the wrapper element never moves in the React tree *or* the DOM — focus mode
 * is a CSS promotion to `position: fixed`, and only the inert backdrop is portalled. The pane's
 * React state, websockets and iframe survive untouched.
 *
 * The one constraint this buys: no ancestor may use a CSS transform, which would make `fixed`
 * resolve against it instead of the viewport. The panel layout is flex-based, so none does.
 */

interface FocusOverlayProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

export function FocusOverlay({ open, title, onClose, children }: FocusOverlayProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusTo = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreFocusTo.current = document.activeElement;
    const previouslyFocused = restoreFocusTo.current;

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusables = focusableWithin(containerRef.current);
      if (focusables.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      // Wrap at both ends, so focus cannot escape to the dimmed page behind the overlay.
      if (event.shiftKey && (active === first || !containerRef.current?.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener('keydown', onKeyDown, true);
    // Move focus in without stealing it from something the pane itself focused on mount.
    const timer = window.setTimeout(() => {
      if (!containerRef.current?.contains(document.activeElement)) {
        focusableWithin(containerRef.current)[0]?.focus();
      }
    }, 0);

    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      window.clearTimeout(timer);
      if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus();
    };
  }, [open, onClose]);

  return (
    <>
      {/* Inert, stateless, and therefore safe to portal. */}
      {open
        ? createPortal(
            <div
              aria-hidden
              className="fixed inset-0 z-40 bg-surface-sunken/95 backdrop-blur-sm"
            />,
            document.body,
          )
        : null}
      <div
        ref={containerRef}
        role={open ? 'dialog' : undefined}
        aria-modal={open ? true : undefined}
        aria-label={open ? `${title} — focus mode` : undefined}
        data-testid={open ? 'focus-overlay' : undefined}
        className={cn(
          'flex min-h-0 flex-1 flex-col',
          open &&
            'fixed inset-3 z-50 overflow-hidden rounded-xl border border-edge-strong bg-surface shadow-2xl',
        )}
      >
        {open ? (
          <div className="flex shrink-0 items-center justify-between border-b border-edge px-3 py-2">
            <span className="text-sm font-medium text-fg">{title}</span>
            <button
              type="button"
              onClick={onClose}
              data-testid="focus-close"
              className="flex items-center gap-1 rounded px-2 py-1 text-xs text-fg-muted hover:bg-surface-raised hover:text-fg"
            >
              <X aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
              Close
              <kbd className="ml-1 rounded border border-edge px-1 text-[10px]">Esc</kbd>
            </button>
          </div>
        ) : null}
        {/* Position in the tree is identical open or closed — that is what keeps children mounted. */}
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
      </div>
    </>
  );
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusableWithin(root: HTMLElement | null): HTMLElement[] {
  if (!root) return [];
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

/** The maximize affordance a pane header renders. */
export function FocusButton({
  onClick,
  label,
  testId,
}: {
  onClick: () => void;
  label: string;
  testId?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      data-testid={testId}
      title={`${label} — full window`}
      aria-label={`${label} — full window`}
      className="rounded p-1 text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg motion-reduce:transition-none"
    >
      <svg
        aria-hidden
        viewBox="0 0 16 16"
        className="h-3.5 w-3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M6 2H2v4M10 14h4v-4M14 6V2h-4M2 10v4h4" />
      </svg>
    </button>
  );
}
