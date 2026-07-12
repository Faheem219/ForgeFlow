import { useEffect } from 'react';
import type { ReactNode } from 'react';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}

export function Modal({ open, onClose, title, children, footer }: ModalProps): JSX.Element | null {
  useEffect(() => {
    if (!open) {
      return;
    }
    function onKey(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        onClose();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : 'Dialog'}
        className="relative w-full max-w-md rounded-xl border border-slate-800 bg-slate-900 shadow-xl"
      >
        {title ? (
          <header className="border-b border-slate-800 px-5 py-4 text-sm font-medium text-slate-100">
            {title}
          </header>
        ) : null}
        <div className="px-5 py-4 text-sm text-slate-300">{children}</div>
        {footer ? (
          <footer className="flex justify-end gap-2 border-t border-slate-800 px-5 py-4">
            {footer}
          </footer>
        ) : null}
      </div>
    </div>
  );
}
