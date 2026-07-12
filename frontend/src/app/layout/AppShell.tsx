import { NavLink, Outlet, useNavigate } from 'react-router-dom';

import { Button } from '../../components/ui';
import { useAuthStore } from '../../lib/stores/authStore';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { useUiStore } from '../../lib/stores/uiStore';

const rtLabel: Record<string, string> = {
  open: 'live',
  connecting: 'connecting',
  closed: 'offline',
};
const rtDot: Record<string, string> = {
  open: 'bg-emerald-400',
  connecting: 'bg-amber-400 animate-pulse',
  closed: 'bg-slate-600',
};

const nav = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/settings', label: 'Settings', end: false },
];

export function AppShell(): JSX.Element {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const user = useAuthStore((s) => s.user);
  const clear = useAuthStore((s) => s.clear);
  const rtStatus = useRealtimeStore((s) => s.status);
  const navigate = useNavigate();

  function logout(): void {
    clear();
    navigate('/login');
  }

  return (
    <div className="flex min-h-screen">
      <aside
        className={`flex flex-col border-r border-slate-800 bg-slate-900/40 transition-all ${
          collapsed ? 'w-16' : 'w-56'
        }`}
      >
        <div className="flex h-14 items-center gap-2 px-4">
          <span
            className="h-6 w-6 shrink-0 rounded bg-gradient-to-br from-brand-500 to-accent-500"
            aria-hidden
          />
          {!collapsed ? <span className="font-semibold text-slate-100">ForgeFlow</span> : null}
        </div>
        <nav className="flex-1 space-y-1 px-2 py-2">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={item.label}
              className={({ isActive }) =>
                `block rounded-lg px-3 py-2 text-sm ${
                  isActive ? 'bg-brand-600/20 text-brand-200' : 'text-slate-300 hover:bg-slate-800'
                }`
              }
            >
              {collapsed ? item.label.charAt(0) : item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-slate-800 px-4">
          <button
            type="button"
            aria-label="Toggle sidebar"
            onClick={toggleSidebar}
            className="rounded p-1.5 text-slate-400 hover:bg-slate-800"
          >
            ☰
          </button>
          <div className="flex items-center gap-4 text-sm">
            <span
              className="flex items-center gap-1.5 text-slate-400"
              title="Realtime connection"
              data-testid="rt-status"
            >
              <span className={`h-2 w-2 rounded-full ${rtDot[rtStatus]}`} aria-hidden />
              {rtLabel[rtStatus]}
            </span>
            <span title="Theme: dark" aria-hidden>
              🌙
            </span>
            <span data-testid="current-user" className="text-slate-300">
              {user?.email}
            </span>
            <Button size="sm" variant="secondary" onClick={logout}>
              Log out
            </Button>
          </div>
        </header>

        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
