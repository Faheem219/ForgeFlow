import { useToastStore, type ToastVariant } from '../../lib/stores/toastStore';

const variantBorder: Record<ToastVariant, string> = {
  info: 'border-brand-600/50',
  success: 'border-emerald-600/50',
  error: 'border-red-600/50',
  warning: 'border-amber-600/50',
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
      {toasts.map((t) => (
        <div
          key={t.id}
          role="status"
          className={`pointer-events-auto rounded-lg border bg-slate-900 px-4 py-3 shadow-lg ${variantBorder[t.variant]}`}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium text-slate-100">{t.title}</p>
              {t.description ? (
                <p className="mt-0.5 text-xs text-slate-400">{t.description}</p>
              ) : null}
            </div>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
              className="text-lg leading-none text-slate-500 hover:text-slate-200"
            >
              ×
            </button>
          </div>
          {t.action ? (
            <button
              type="button"
              onClick={() => {
                t.action?.onClick();
                dismiss(t.id);
              }}
              className="mt-2 text-xs font-medium text-brand-400 hover:underline"
            >
              {t.action.label}
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}
