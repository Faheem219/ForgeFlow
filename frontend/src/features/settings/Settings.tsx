import { ArrowRight } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { Badge } from '../../components/ui';
import { useAuthStore } from '../../lib/stores/authStore';
import Credentials from './Credentials';

/**
 * One settings section: what it is and why, on the left; the controls on the right. The reading
 * order a settings page actually needs — the explanation before the inputs, not buried under them.
 */
function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: ReactNode;
  children: ReactNode;
}): JSX.Element {
  return (
    <section className="grid gap-x-10 gap-y-4 border-t border-edge py-8 first:border-t-0 first:pt-2 md:grid-cols-[15rem_minmax(0,1fr)]">
      <div>
        <h2 className="text-sm font-semibold text-fg">{title}</h2>
        <div className="mt-1 text-[13px] leading-relaxed text-fg-subtle">{description}</div>
      </div>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

export default function Settings(): JSX.Element {
  const user = useAuthStore((s) => s.user);

  return (
    <div className="mx-auto max-w-4xl">
      <div className="mb-6">
        <h1 className="text-[22px] font-semibold tracking-tight text-fg">Settings</h1>
        <p className="mt-1 text-[13px] text-fg-subtle">
          Your own provider tokens and account. Platform-wide configuration lives in Admin.
        </p>
      </div>

      <Section
        title="Provider credentials"
        description="Bring your own Vercel, Render, MongoDB, Stitch or Figma account instead of the platform's."
      >
        <Credentials />
      </Section>

      <Section title="Account" description="Who you are signed in as, and what you can change.">
        <dl className="divide-y divide-edge overflow-hidden rounded-xl border border-edge bg-surface">
          <div className="flex items-center justify-between gap-4 px-4 py-3">
            <dt className="text-[13px] text-fg-subtle">Signed in as</dt>
            <dd className="truncate text-[13px] font-medium text-fg">{user?.email ?? '—'}</dd>
          </div>
          <div className="flex items-center justify-between gap-4 px-4 py-3">
            <dt className="text-[13px] text-fg-subtle">Role</dt>
            <dd>
              <Badge tone={user?.role === 'admin' ? 'brand' : 'neutral'}>
                <span className="capitalize">{user?.role ?? '—'}</span>
              </Badge>
            </dd>
          </div>
        </dl>
        {user?.role === 'admin' ? (
          <p className="mt-3 text-[13px] text-fg-subtle">
            <Link
              to="/admin"
              className="group inline-flex items-center gap-1 font-medium text-fg underline-offset-2 hover:underline"
            >
              Open the platform settings
              <ArrowRight
                aria-hidden
                className="h-3.5 w-3.5 transition-transform duration-200 group-hover:translate-x-0.5"
              />
            </Link>{' '}
            to configure models, providers, budgets and limits.
          </p>
        ) : null}
      </Section>
    </div>
  );
}
