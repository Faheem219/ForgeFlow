import type { ReactNode } from 'react';
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

/** Inline stroke icons — the sidebar collapses to icons only, so each nav item needs a real one. */
function Icon({ path }: { path: ReactNode }): JSX.Element {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-[18px] w-[18px] shrink-0"
      aria-hidden
    >
      {path}
    </svg>
  );
}

const ICONS: Record<string, ReactNode> = {
  dashboard: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  eval: (
    <>
      <path d="M3 3v18h18" />
      <path d="M7 15l4-5 3 3 5-7" />
    </>
  ),
  settings: (
    <>
      <path d="M4 6h16M4 12h16M4 18h16" />
      <circle cx="9" cy="6" r="2" />
      <circle cx="15" cy="12" r="2" />
      <circle cx="8" cy="18" r="2" />
    </>
  ),
  admin: (
    <>
      <path d="M12 3l7 3v6c0 4.4-3 8.2-7 9-4-.8-7-4.6-7-9V6z" />
      <path d="M9.5 12l1.8 1.8L15 10" />
    </>
  ),
};

interface NavItem {
  to: string;
  label: string;
  end: boolean;
  icon: string;
  adminOnly?: boolean;
}

const nav: NavItem[] = [
  { to: '/', label: 'Dashboard', end: true, icon: 'dashboard' },
  { to: '/eval', label: 'Evaluation', end: false, icon: 'eval' },
  { to: '/settings', label: 'Settings', end: false, icon: 'settings' },
  { to: '/admin', label: 'Admin', end: false, icon: 'admin', adminOnly: true },
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
    <div className="flex h-screen overflow-hidden">
      <aside
        className={`flex shrink-0 flex-col border-r border-slate-800 bg-slate-900/40 transition-all ${
          collapsed ? 'w-16' : 'w-56'
        }`}
      >
        <div
          className={`flex h-14 shrink-0 items-center gap-2 ${collapsed ? 'justify-center px-0' : 'px-4'}`}
        >
          <span
            className="h-7 w-7 shrink-0 rounded-lg bg-gradient-to-br from-brand-500 to-accent-500"
            aria-hidden
          />
          {!collapsed ? (
            <span className="font-semibold tracking-tight text-slate-100">ForgeFlow</span>
          ) : null}
        </div>

        <nav className="flex-1 space-y-1 px-2 py-2">
          {nav
            .filter((item) => !item.adminOnly || user?.role === 'admin')
            .map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                title={item.label}
                className={({ isActive }) =>
                  `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition ${
                    collapsed ? 'justify-center' : ''
                  } ${
                    isActive
                      ? 'bg-brand-600/20 font-medium text-brand-200'
                      : 'text-slate-400 hover:bg-slate-800/70 hover:text-slate-100'
                  }`
                }
              >
                <Icon path={ICONS[item.icon]} />
                {!collapsed ? <span className="truncate">{item.label}</span> : null}
              </NavLink>
            ))}
        </nav>

        {/* Build version — lets a user confirm an update actually took effect (phase-49). */}
        <div
          className="shrink-0 px-4 py-2 text-xs text-slate-600"
          title={`ForgeFlow v${__APP_VERSION__}`}
          data-testid="app-version"
        >
          {collapsed ? `v` : `v${__APP_VERSION__}`}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-slate-800 px-4">
          <button
            type="button"
            aria-label="Toggle sidebar"
            onClick={toggleSidebar}
            className="rounded-lg p-2 text-slate-400 transition hover:bg-slate-800 hover:text-slate-100"
          >
            <Icon
              path={
                <>
                  <path d="M4 6h16M4 12h16M4 18h16" />
                </>
              }
            />
          </button>
          <div className="flex items-center gap-3 text-sm">
            <span
              className="flex items-center gap-1.5 rounded-full border border-slate-800 px-2.5 py-1 text-xs text-slate-400"
              title="Realtime connection"
              data-testid="rt-status"
            >
              <span className={`h-1.5 w-1.5 rounded-full ${rtDot[rtStatus]}`} aria-hidden />
              {rtLabel[rtStatus]}
            </span>
            <span title="Theme: dark" aria-hidden>
              🌙
            </span>
            <span
              data-testid="current-user"
              className="hidden max-w-[16rem] truncate text-slate-300 sm:block"
            >
              {user?.email}
            </span>
            <Button size="sm" variant="secondary" onClick={logout}>
              Log out
            </Button>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
