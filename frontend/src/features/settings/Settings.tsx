import { Link } from 'react-router-dom';

import { Panel } from '../../components/ui';
import { useAuthStore } from '../../lib/stores/authStore';
import Credentials from './Credentials';

export default function Settings(): JSX.Element {
  const user = useAuthStore((s) => s.user);

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Settings</h1>
        <p className="text-sm text-slate-400">
          Your own provider tokens and account. Platform-wide configuration lives in Admin.
        </p>
      </div>

      <Credentials />

      <Panel title="Account">
        <dl className="grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-500">Signed in as</dt>
            <dd className="mt-0.5 truncate text-sm text-slate-200">{user?.email ?? '—'}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-slate-500">Role</dt>
            <dd className="mt-0.5 text-sm capitalize text-slate-200">{user?.role ?? '—'}</dd>
          </div>
        </dl>
        {user?.role === 'admin' ? (
          <p className="mt-4 border-t border-slate-800 pt-3 text-sm text-slate-400">
            You are an admin —{' '}
            <Link to="/admin" className="text-brand-300 underline-offset-2 hover:underline">
              open the platform settings
            </Link>{' '}
            to configure models, providers, budgets and limits.
          </p>
        ) : null}
      </Panel>
    </div>
  );
}
