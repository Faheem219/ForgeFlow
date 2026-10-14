import { motion } from 'framer-motion';
import { CircleAlert, CircleCheck, CircleX, Info, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { useToastStore, type ToastVariant } from '../../lib/stores/toastStore';

/*
 * The variant is said by a small status glyph beside the title, not by a coloured stripe down the
 * card's edge — the edge stripe is the single most recognisable template tell, and the glyph is
 * also the one that survives for colour-blind readers.
 */
const variantIcon: Record<ToastVariant, { icon: LucideIcon; tone: string }> = {
  info: { icon: Info, tone: 'text-info' },
  success: { icon: CircleCheck, tone: 'text-success' },
  error: { icon: CircleX, tone: 'text-danger' },
  warning: { icon: CircleAlert, tone: 'text-warning' },
};

/** Global toast overlay. Mounted once at the app root (see providers.tsx). */
export function Toaster(): JSX.Element {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-full max-w-[22rem] flex-col gap-2"
      role="region"
      aria-label="Notifications"
    >
      {/* Entrance-only animation: dismissal must be synchronous — code all over the app (and its
          tests) treats a dismissed toast as gone NOW, and an exit animation would hold a ghost. */}
      {toasts.map((t) => {
        const { icon: Icon, tone } = variantIcon[t.variant];
        return (
          <motion.div
            key={t.id}
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            role="status"
            className="pointer-events-auto rounded-xl bg-surface-overlay px-3.5 py-3 shadow-pop"
          >
            <div className="flex items-start gap-2.5">
              <Icon aria-hidden className={`mt-px h-4 w-4 shrink-0 ${tone}`} strokeWidth={2} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg">{t.title}</p>
                {t.description ? (
                  <p className="mt-0.5 break-words text-xs leading-relaxed text-fg-subtle">
                    {t.description}
                  </p>
                ) : null}
                {t.action ? (
                  <button
                    type="button"
                    onClick={() => {
                      t.action?.onClick();
                      dismiss(t.id);
                    }}
                    className="mt-2 text-xs font-medium text-brand-text hover:underline"
                  >
                    {t.action.label}
                  </button>
                ) : null}
              </div>
              <button
                type="button"
                aria-label="Dismiss"
                onClick={() => dismiss(t.id)}
                className="-m-1 rounded-md p-1 text-fg-faint transition-colors hover:bg-surface-raised hover:text-fg"
              >
                <X aria-hidden className="h-3.5 w-3.5" />
              </button>
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}
