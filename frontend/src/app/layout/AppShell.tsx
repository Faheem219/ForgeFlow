import { ChartNoAxesColumn, LayoutGrid, PanelLeft, Settings2, ShieldCheck } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';

import { Button } from '../../components/ui';
import { cn } from '../../lib/cn';
import { useAuthStore } from '../../lib/stores/authStore';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { useUiStore } from '../../lib/stores/uiStore';
import { ThemeToggle } from './ThemeToggle';

const rtLabel: Record<string, string> = {
  open: 'live',
  connecting: 'connecting',
  closed: 'offline',
};
const rtDot: Record<string, string> = {
  open: 'bg-success',
  connecting: 'bg-warning animate-pulse',
  closed: 'bg-fg-faint',
};

interface NavItem {
  to: string;
  label: string;
  end: boolean;
  icon: LucideIcon;
  adminOnly?: boolean;
}

const nav: NavItem[] = [
  { to: '/', label: 'Dashboard', end: true, icon: LayoutGrid },
  { to: '/eval', label: 'Evaluation', end: false, icon: ChartNoAxesColumn },
  { to: '/settings', label: 'Settings', end: false, icon: Settings2 },
  { to: '/admin', label: 'Admin', end: false, icon: ShieldCheck, adminOnly: true },
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
    <div className="flex h-screen overflow-hidden bg-surface-canvas">
      <aside
        className={cn(
          'flex shrink-0 flex-col border-r border-edge bg-surface transition-all duration-200',
          collapsed ? 'w-16' : 'w-56',
        )}
      >
        <div
          className={cn(
            'flex h-14 shrink-0 items-center gap-2.5',
            collapsed ? 'justify-center px-0' : 'px-4',
          )}
        >
          <span
            className="h-7 w-7 shrink-0 rounded-lg bg-gradient-to-br from-brand to-accent shadow-sm"
            aria-hidden
          />
          {!collapsed ? (
            <span className="font-semibold tracking-tight text-fg">ForgeFlow</span>
          ) : null}
        </div>

        <nav className="flex-1 space-y-0.5 px-2 py-2">
          {nav
            .filter((item) => !item.adminOnly || user?.role === 'admin')
            .map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                title={item.label}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
                    collapsed && 'justify-center',
                    isActive
                      ? 'bg-brand/10 font-medium text-brand'
                      : 'text-fg-muted hover:bg-surface-raised hover:text-fg',
                  )
                }
              >
                <item.icon aria-hidden className="h-[18px] w-[18px] shrink-0" strokeWidth={1.75} />
                {!collapsed ? <span className="truncate">{item.label}</span> : null}
              </NavLink>
            ))}
        </nav>

        {/* Build version — lets a user confirm an update actually took effect (phase-49). */}
        <div
          className="shrink-0 px-4 py-2 text-xs text-fg-faint"
          title={`ForgeFlow v${__APP_VERSION__}`}
          data-testid="app-version"
        >
          {collapsed ? `v` : `v${__APP_VERSION__}`}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-edge px-4">
          <button
            type="button"
            aria-label="Toggle sidebar"
            onClick={toggleSidebar}
            className="rounded-lg p-2 text-fg-muted transition-colors hover:bg-surface-raised hover:text-fg"
          >
            <PanelLeft aria-hidden className="h-[18px] w-[18px]" strokeWidth={1.75} />
          </button>
          <div className="flex items-center gap-3 text-sm">
            <span
              className="flex items-center gap-1.5 rounded-full border border-edge px-2.5 py-1 text-xs text-fg-muted"
              title="Realtime connection"
              data-testid="rt-status"
            >
              <span className={`h-1.5 w-1.5 rounded-full ${rtDot[rtStatus]}`} aria-hidden />
              {rtLabel[rtStatus]}
            </span>
            <ThemeToggle />
            <span
              data-testid="current-user"
              className="hidden max-w-[16rem] truncate text-fg-muted sm:block"
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
