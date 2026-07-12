import { useNavigate } from 'react-router-dom';

import { HealthBadge } from '../../components/HealthBadge';
import { useAuthStore } from '../../lib/stores/authStore';

export default function Dashboard(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const clear = useAuthStore((s) => s.clear);
  const navigate = useNavigate();

  function logout(): void {
    clear();
    navigate('/login');
  }

  return (
    <main className="min-h-screen p-8">
      <header className="mb-10 flex items-center justify-between">
        <h1 className="bg-gradient-to-r from-brand-400 to-accent-400 bg-clip-text text-2xl font-semibold text-transparent">
          ForgeFlow
        </h1>
        <div className="flex items-center gap-4 text-sm text-slate-300">
          <span data-testid="current-user">
            {user?.email}
            {user?.role === 'admin' ? ' · admin' : ''}
          </span>
          <button
            type="button"
            onClick={logout}
            className="rounded-lg border border-slate-700 px-3 py-1.5 hover:bg-slate-800"
          >
            Log out
          </button>
        </div>
      </header>

      <section className="space-y-4">
        <p className="text-slate-400">
          You&apos;re signed in. Your project workspace lands here as later phases ship.
        </p>
        <HealthBadge />
      </section>
    </main>
  );
}
