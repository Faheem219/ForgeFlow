import { useEffect } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';

import { Kbd, Tooltip } from '../../components/ui';
import {
  IconAdmin,
  IconDashboard,
  IconEval,
  IconLogout,
  IconSearch,
  IconSettings,
  IconSidebar,
} from '../../components/ui/Icons';
import CommandPalette from '../../features/command/CommandPalette';
import { useAuthStore } from '../../lib/stores/authStore';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { watchSystemTheme } from '../../lib/stores/themeStore';
import { useUiStore } from '../../lib/stores/uiStore';
import { ThemeToggle } from './ThemeToggle';

/**
 * Application chrome.
 *
 * The sidebar is a fixed rail, not a floating card: chrome that reads as *part of the window*
 * rather than as another content box is what separates a tool from a marketing page. The active
 * item is marked by a 2px spine on the rail edge — a hairline that follows the pointer's mental
 * model of "you are here" without lighting up a whole pill.
 */

interface NavItem {
  to: string;
  label: string;
  end: boolean;
  icon: (props: { size?: number }) => JSX.Element;
  adminOnly?: boolean;
}

const nav: NavItem[] = [
  { to: '/', label: 'Projects', end: true, icon: IconDashboard },
  { to: '/eval', label: 'Evaluation', end: false, icon: IconEval },
  { to: '/settings', label: 'Settings', end: false, icon: IconSettings },
  { to: '/admin', label: 'Admin', end: false, icon: IconAdmin, adminOnly: true },
];

const RT_LABEL: Record<string, string> = {
  open: 'Live',
  connecting: 'Connecting',
  closed: 'Offline',
};
const RT_DOT: Record<string, string> = {
  open: 'bg-success',
  connecting: 'bg-warning motion-essential animate-pulse-dot',
  closed: 'bg-faint',
};

export function AppShell(): JSX.Element {
  const collapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const setCommandOpen = useUiStore((s) => s.setCommandOpen);
  const user = useAuthStore((s) => s.user);
  const clear = useAuthStore((s) => s.clear);
  const rtStatus = useRealtimeStore((s) => s.status);
  const navigate = useNavigate();
  const { pathname } = useLocation();

  // Follow the OS theme for as long as the shell is mounted (only acts in `system` mode).
  useEffect(() => watchSystemTheme(), []);

  function logout(): void {
    clear();
    navigate('/login');
  }

  return (
    <div className="flex h-screen overflow-hidden bg-canvas">
      <aside
        className={`flex shrink-0 flex-col border-r border-subtle bg-surface transition-[width] duration-base ease-out ${
          collapsed ? 'w-[3.25rem]' : 'w-[13.5rem]'
        }`}
      >
        <div className="flex h-12 items-center gap-2.5 px-3.5">
          <span
            className="grid h-[1.375rem] w-[1.375rem] shrink-0 place-items-center rounded-[0.3rem] bg-brand text-[0.625rem] font-bold text-brand-fg"
            aria-hidden
          >
            FF
          </span>
          {!collapsed ? (
            <span className="truncate text-sm font-semibold tracking-tight text-primary">
              ForgeFlow
            </span>
          ) : null}
        </div>

        <nav className="flex-1 space-y-0.5 px-2 py-1.5">
          {nav
            .filter((item) => !item.adminOnly || user?.role === 'admin')
            .map((item) => {
              const Glyph = item.icon;
              const link = (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  title={collapsed ? item.label : undefined}
                  className={({ isActive }) =>
                    `relative flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors duration-fast ease-out ${
                      isActive
                        ? 'bg-elevated font-medium text-primary'
                        : 'text-muted hover:bg-elevated hover:text-secondary'
                    } ${collapsed ? 'justify-center px-0' : ''}`
                  }
                >
                  {({ isActive }) => (
                    <>
                      {/* The "you are here" spine, anchored to the rail edge. */}
                      <span
                        aria-hidden
                        className={`absolute -left-2 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r-full bg-brand transition-opacity duration-fast ease-out ${
                          isActive ? 'opacity-100' : 'opacity-0'
                        }`}
                      />
                      <Glyph size={15} />
                      {!collapsed ? <span className="truncate">{item.label}</span> : null}
                    </>
                  )}
                </NavLink>
              );
              return link;
            })}
        </nav>

        <div className="space-y-1 border-t border-subtle p-2">
          <button
            type="button"
            onClick={() => setCommandOpen(true)}
            data-testid="open-command-palette"
            className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm text-muted transition-colors duration-fast ease-out hover:bg-elevated hover:text-secondary ${
              collapsed ? 'justify-center px-0' : ''
            }`}
            title="Search — Ctrl/⌘ K"
          >
            <IconSearch size={15} />
            {!collapsed ? (
              <>
                <span className="flex-1 text-left">Search</span>
                <Kbd>⌘K</Kbd>
              </>
            ) : null}
          </button>
          {/* Build version — lets a user confirm an update actually took effect (phase-49). */}
          <p
            className="px-2.5 text-2xs text-faint"
            title={`ForgeFlow v${__APP_VERSION__}`}
            data-testid="app-version"
          >
            {collapsed ? 'v' : `v${__APP_VERSION__}`}
          </p>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-subtle px-3">
          <Tooltip label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            <button
              type="button"
              aria-label="Toggle sidebar"
              onClick={toggleSidebar}
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors duration-fast ease-out hover:bg-elevated hover:text-primary"
            >
              <IconSidebar size={15} />
            </button>
          </Tooltip>

          <div className="flex items-center gap-2.5">
            <span
              className="flex items-center gap-1.5 text-2xs text-muted"
              title="Realtime connection"
              data-testid="rt-status"
            >
              <span className={`h-1.5 w-1.5 rounded-full ${RT_DOT[rtStatus]}`} aria-hidden />
              {RT_LABEL[rtStatus]}
            </span>

            <span aria-hidden className="h-4 w-px bg-subtle" />

            <ThemeToggle />

            <span aria-hidden className="h-4 w-px bg-subtle" />

            <span data-testid="current-user" className="max-w-[14rem] truncate text-xs text-muted">
              {user?.email}
            </span>
            <Tooltip label="Log out">
              <button
                type="button"
                onClick={logout}
                aria-label="Log out"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted transition-colors duration-fast ease-out hover:bg-elevated hover:text-primary"
              >
                <IconLogout size={15} />
              </button>
            </Tooltip>
          </div>
        </header>

        {/*
          Route transition. Keying on pathname remounts the wrapper, which re-triggers the entrance
          animation — the same effect as a transition library for this case, with no dependency and
          no risk of a stale exit animation trapping a route that has already unmounted. Reduced
          motion collapses the duration to ~0 via the token override.
        */}
        <main key={pathname} className="min-h-0 flex-1 animate-rise-in overflow-auto p-5">
          <Outlet />
        </main>
      </div>

      <CommandPalette />
    </div>
  );
}
