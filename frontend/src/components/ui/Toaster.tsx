import { motion } from 'framer-motion';
import { X } from 'lucide-react';

import { useToastStore, type ToastVariant } from '../../lib/stores/toastStore';

const variantAccent: Record<ToastVariant, string> = {
  info: 'bg-brand',
  success: 'bg-success',
  error: 'bg-danger',
  warning: 'bg-warning',
};

/** Global toast overlay. Mounted once at the app root (see providers.tsx). */
export function Toaster(): JSX.Element {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2"
      role="region"
      aria-label="Notifications"
    >
      {/* Entrance-only animation: dismissal must be synchronous — code all over the app (and its
          tests) treats a dismissed toast as gone NOW, and an exit animation would hold a ghost. */}
      {toasts.map((t) => (
        <motion.div
          key={t.id}
          initial={{ opacity: 0, x: 24, scale: 0.97 }}
          animate={{ opacity: 1, x: 0, scale: 1 }}
          transition={{ duration: 0.18, ease: [0.16, 1, 0.3, 1] }}
          role="status"
          className="pointer-events-auto relative overflow-hidden rounded-lg border border-edge bg-surface-overlay py-3 pl-5 pr-4 shadow-xl"
        >
          {/* Variant is a slim accent bar, not a tinted border — quieter, readable at a glance. */}
          <span
            aria-hidden
            className={`absolute inset-y-0 left-0 w-1 ${variantAccent[t.variant]}`}
          />
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-fg">{t.title}</p>
              {t.description ? (
                <p className="mt-0.5 text-xs text-fg-subtle">{t.description}</p>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
              className="-m-1 rounded p-1 text-fg-faint transition-colors hover:bg-surface-raised hover:text-fg"
            >
              <X aria-hidden className="h-3.5 w-3.5" />
            </button>
          </div>
          {t.action ? (
            <button
              type="button"
              onClick={() => {
                t.action?.onClick();
                dismiss(t.id);
              }}
              className="mt-2 text-xs font-medium text-brand hover:underline"
            >
              {t.action.label}
            </button>
          ) : null}
        </motion.div>
      ))}
    </div>
  );
}
