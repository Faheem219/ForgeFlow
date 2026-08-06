import { motion } from 'framer-motion';
import {
  ChartNoAxesColumn,
  LayoutGrid,
  LogOut,
  PanelLeft,
  Search,
  Settings2,
  ShieldCheck,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';

import { AmbientBackground } from '../../components/AmbientBackground';
import { BrandMark } from '../../components/BrandMark';
import { Button, Kbd } from '../../components/ui';
import { CommandPalette } from '../CommandPalette';
import { cn } from '../../lib/cn';
import { EASE_OUT, gsap, motionOK, useGSAP } from '../../lib/gsap';
import { EASE } from '../../lib/motion';
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
  { to: '/dashboard', label: 'Dashboard', end: true, icon: LayoutGrid },
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
  const location = useLocation();
  const mainRef = useRef<HTMLDivElement | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);

  // ⌘K / Ctrl+K opens the palette anywhere in the app — except inside Monaco, which owns chords.
  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        if ((event.target as HTMLElement | null)?.closest?.('.monaco-editor')) return;
        event.preventDefault();
        setPaletteOpen((v) => !v);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Each route change re-enters the content with a short rise — the shell itself never moves.
  useGSAP(
    () => {
      if (!motionOK() || !mainRef.current) return;
      gsap.fromTo(
        mainRef.current,
        { opacity: 0, y: 10 },
        { opacity: 1, y: 0, duration: 0.35, ease: EASE_OUT, clearProps: 'transform' },
      );
    },
    { dependencies: [location.pathname] },
  );

  function logout(): void {
    clear();
    navigate('/login');
  }

  return (
    <div className="flex h-screen overflow-hidden bg-surface-canvas">
      <aside
        className={cn(
          'relative flex shrink-0 flex-col border-r border-edge bg-surface transition-all duration-200',
          collapsed ? 'w-16' : 'w-56',
        )}
      >
        <div
          className={cn(
            'relative flex h-14 shrink-0 items-center gap-2.5',
            collapsed ? 'justify-center px-0' : 'px-4',
          )}
        >
          <BrandMark size={28} />
          {!collapsed ? (
            <span className="font-semibold tracking-tight text-fg">ForgeFlow</span>
          ) : null}
        </div>

        <nav className="relative flex-1 space-y-0.5 px-2 py-2">
          {!collapsed ? (
            <p className="px-3 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-fg-faint">
              Workspace
            </p>
          ) : null}
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
                    'relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors',
                    collapsed && 'justify-center',
                    isActive
                      ? 'font-medium text-brand-text'
                      : 'text-fg-muted hover:bg-surface-raised hover:text-fg',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    {isActive ? (
                      // One shared pill slides between items rather than blinking in per link.
                      <motion.span
                        layoutId="sidebar-active-pill"
                        aria-hidden
                        className="absolute inset-0 rounded-lg bg-brand/10 ring-1 ring-inset ring-brand/20"
                        transition={{ duration: 0.25, ease: EASE }}
                      />
                    ) : null}
                    <item.icon
                      aria-hidden
                      className="relative h-[18px] w-[18px] shrink-0"
                      strokeWidth={1.75}
                    />
                    {!collapsed ? <span className="relative truncate">{item.label}</span> : null}
                  </>
                )}
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

      {/* The ambient layer lives on the non-scrolling column, not inside `main`: anchored to the
          scroll container it would slide away on a long page, taking the pointer glow with it. */}
      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        <AmbientBackground intensity="subtle" />

        <header className="relative flex h-14 shrink-0 items-center justify-between gap-4 border-b border-edge px-4">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              aria-label="Toggle sidebar"
              onClick={toggleSidebar}
              className="rounded-lg p-2 text-fg-muted transition-colors hover:bg-surface-raised hover:text-fg"
            >
              <PanelLeft aria-hidden className="h-[18px] w-[18px]" strokeWidth={1.75} />
            </button>
            <button
              type="button"
              data-testid="command-trigger"
              onClick={() => setPaletteOpen(true)}
              className="hidden h-8 w-56 items-center gap-2 rounded-lg border border-edge bg-surface-raised/60 px-2.5 text-xs text-fg-subtle transition-colors hover:border-edge-strong hover:text-fg-muted md:flex"
            >
              <Search aria-hidden className="h-3.5 w-3.5 shrink-0" />
              <span className="flex-1 truncate text-left">Search or jump to…</span>
              <Kbd>Ctrl K</Kbd>
            </button>
          </div>
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
            <span className="hidden items-center gap-2 sm:flex">
              <span
                aria-hidden
                className="flex h-7 w-7 items-center justify-center rounded-full bg-brand text-xs font-semibold text-fg-inverted"
              >
                {(user?.email ?? '?').charAt(0).toUpperCase()}
              </span>
              <span data-testid="current-user" className="max-w-[14rem] truncate text-fg-muted">
                {user?.email}
              </span>
            </span>
            <Button size="sm" variant="secondary" onClick={logout}>
              <LogOut aria-hidden className="h-3.5 w-3.5" />
              Log out
            </Button>
          </div>
        </header>

        <main data-app-scroll className="relative min-h-0 flex-1 overflow-auto p-6">
          <div ref={mainRef} className="h-full">
            <Outlet />
          </div>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
