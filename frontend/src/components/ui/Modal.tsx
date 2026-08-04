import { AnimatePresence, motion } from 'framer-motion';
import { useCallback, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * Modal dialog.
 *
 * Rendered through a portal (so it can never be clipped by an `overflow-hidden` ancestor) with
 * three behaviors that are correctness rather than polish:
 *  - **Focus trap.** Tab cycles inside the dialog; without it a keyboard user tabs into the page
 *    behind the scrim and cannot tell where they are.
 *  - **Focus restore.** The element that opened the dialog gets focus back on close.
 *  - **Scroll lock.** The page behind the scrim no longer scrolls under the pointer.
 *
 * Escape is handled on `window` (not the dialog node) so it fires regardless of what has focus,
 * including a Monaco editor rendered inside the dialog.
 */

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export type ModalSize = 'sm' | 'md' | 'lg';

const SIZES: Record<ModalSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-2xl',
};

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  size?: ModalSize;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = 'md',
}: ModalProps): JSX.Element {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  const trapFocus = useCallback((event: KeyboardEvent) => {
    const dialog = dialogRef.current;
    if (!dialog || event.key !== 'Tab') return;
    const targets = Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
      (el) => el.offsetParent !== null || el === document.activeElement,
    );
    if (targets.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }
    const first = targets[0];
    const last = targets[targets.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === dialog)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }, []);

  useEffect(() => {
    if (!open) return;

    restoreTo.current = document.activeElement as HTMLElement | null;
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';

    function onKey(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }
      trapFocus(event);
    }
    window.addEventListener('keydown', onKey);

    // Focus the first control, else the dialog itself, so screen readers announce the right thing.
    const dialog = dialogRef.current;
    const firstField = dialog?.querySelector<HTMLElement>(FOCUSABLE);
    (firstField ?? dialog)?.focus();

    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
      restoreTo.current?.focus?.();
    };
  }, [open, onClose, trapFocus]);

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
            onClick={onClose}
            aria-hidden
          />
          <motion.div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            tabIndex={-1}
            aria-label={typeof title === 'string' ? title : 'Dialog'}
            initial={{ opacity: 0, scale: 0.96, y: 8 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: 4 }}
            transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
            className={`relative w-full rounded-xl border border-edge bg-surface-overlay shadow-2xl outline-none ${SIZES[size]}`}
          >
            {title ? (
              <header className="border-b border-edge px-4 py-3">
                <h2 className="text-sm font-semibold text-fg">{title}</h2>
                {description ? <p className="mt-1 text-xs text-fg-subtle">{description}</p> : null}
              </header>
            ) : null}
            <div className="px-4 py-3.5 text-sm text-fg-muted">{children}</div>
            {footer ? (
              <footer className="flex justify-end gap-2 border-t border-edge px-4 py-3">
                {footer}
              </footer>
            ) : null}
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
