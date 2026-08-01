import { useToastStore, type ToastVariant } from '../../lib/stores/toastStore';

/**
 * Global toast overlay. Mounted once at the app root (see providers.tsx).
 *
 * A left accent bar carries the variant instead of tinting the whole card: at the corner of the
 * screen a fully-tinted card competes with the app, while a 2px rule is readable at a glance and
 * stays legible on both themes.
 */

const accent: Record<ToastVariant, string> = {
  info: 'bg-info',
  success: 'bg-success',
  error: 'bg-danger',
  warning: 'bg-warning',
};

export function Toaster(): JSX.Element {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-[22rem] flex-col gap-2"
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className="on-surface pointer-events-auto flex animate-slide-in-right overflow-hidden rounded-lg border border-line bg-overlay shadow-overlay"
        >
          <span aria-hidden className={`w-0.5 shrink-0 ${accent[t.variant]}`} />
          <div className="min-w-0 flex-1 px-3 py-2.5">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm font-medium text-primary">{t.title}</p>
                {t.description ? (
                  <p className="mt-0.5 break-words text-xs text-muted">{t.description}</p>
                ) : null}
              </div>
              <button
                type="button"
                aria-label="Dismiss"
                onClick={() => dismiss(t.id)}
                className="-mr-1 -mt-0.5 shrink-0 rounded p-1 text-faint transition-colors duration-fast ease-out hover:bg-elevated hover:text-primary"
              >
                <svg viewBox="0 0 16 16" className="h-3 w-3" aria-hidden>
                  <path
                    d="M4 4l8 8M12 4l-8 8"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    strokeLinecap="round"
                    fill="none"
                  />
                </svg>
              </button>
            </div>
            {t.action ? (
              <button
                type="button"
                onClick={() => {
                  t.action?.onClick();
                  dismiss(t.id);
                }}
                className="mt-1.5 text-xs font-medium text-brand-text transition-opacity duration-fast ease-out hover:opacity-80"
              >
                {t.action.label}
              </button>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
