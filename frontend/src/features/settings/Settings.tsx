import { ArrowRight, Mail, ShieldCheck } from 'lucide-react';
import { useRef } from 'react';
import { Link } from 'react-router-dom';

import { Panel } from '../../components/ui';
import { EASE_OUT, gsap, motionOK, useGSAP } from '../../lib/gsap';
import { useAuthStore } from '../../lib/stores/authStore';
import Credentials from './Credentials';

export default function Settings(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const scope = useRef<HTMLDivElement | null>(null);

  // Sections rise in once on entry — same beat as the dashboard, so page changes feel related.
  useGSAP(
    () => {
      if (!motionOK()) return;
      gsap.from('[data-settings-section]', {
        opacity: 0,
        y: 16,
        duration: 0.45,
        ease: EASE_OUT,
        stagger: 0.08,
        clearProps: 'all',
      });
    },
    { scope },
  );

  return (
    <div ref={scope} className="mx-auto max-w-4xl space-y-6">
      <div data-settings-section>
        <h1 className="text-2xl font-semibold tracking-tight text-fg">Settings</h1>
        <p className="mt-0.5 text-sm text-fg-muted">
          Your own provider tokens and account. Platform-wide configuration lives in Admin.
        </p>
      </div>

      <div data-settings-section>
        <Credentials />
      </div>

      <div data-settings-section>
        <Panel title="Account">
          <dl className="grid gap-3 sm:grid-cols-2">
            <div className="flex items-center gap-3 rounded-xl border border-edge bg-surface-sunken/50 px-3 py-2.5">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-brand/20 bg-brand/10 text-brand-text">
                <Mail aria-hidden className="h-4 w-4" strokeWidth={1.75} />
              </span>
              <span className="min-w-0">
                <dt className="text-[11px] uppercase tracking-wide text-fg-faint">Signed in as</dt>
                <dd className="truncate text-sm font-medium text-fg">{user?.email ?? '—'}</dd>
              </span>
            </div>
            <div className="flex items-center gap-3 rounded-xl border border-edge bg-surface-sunken/50 px-3 py-2.5">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-brand/20 bg-brand/10 text-brand-text">
                <ShieldCheck aria-hidden className="h-4 w-4" strokeWidth={1.75} />
              </span>
              <span className="min-w-0">
                <dt className="text-[11px] uppercase tracking-wide text-fg-faint">Role</dt>
                <dd className="text-sm font-medium capitalize text-fg">{user?.role ?? '—'}</dd>
              </span>
            </div>
          </dl>
          {user?.role === 'admin' ? (
            <div className="mt-4 border-t border-edge pt-3">
              <Link
                to="/admin"
                className="group inline-flex items-center gap-1.5 text-sm text-brand-text underline-offset-2 hover:underline"
              >
                Open the platform settings
                <ArrowRight
                  aria-hidden
                  className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5"
                />
              </Link>
              <span className="ml-1.5 text-sm text-fg-muted">
                to configure models, providers, budgets and limits.
              </span>
            </div>
          ) : null}
        </Panel>
      </div>
    </div>
  );
}
