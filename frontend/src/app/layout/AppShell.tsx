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

import { BrandMark } from '../../components/BrandMark';
import { IconButton, Kbd, Tooltip } from '../../components/ui';
import { CommandPalette } from '../CommandPalette';
import { cn } from '../../lib/cn';
import { EASE, fadeInElement } from '../../lib/motion';
import { useMediaQuery } from '../../lib/useNarrow';
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
  connecting: 'bg-warning motion-safe:animate-ff-breathe',
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

/** ⌘ on Apple keyboards, Ctrl elsewhere — the hint should name the key the user will press. */
const MOD_KEY =
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/i.test(navigator.platform ?? '')
    ? '⌘'
    : 'Ctrl';

export function AppShell(): JSX.Element {
  const storedCollapsed = useUiStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const user = useAuthStore((s) => s.user);
  const clear = useAuthStore((s) => s.clear);
  const rtStatus = useRealtimeStore((s) => s.status);
  const navigate = useNavigate();
  const location = useLocation();
  const mainRef = useRef<HTMLDivElement | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);

  // The project workspace is a workbench: it wants the window edge-to-edge, not a page margin.
  const workbench = location.pathname.startsWith('/projects/');

  // On a phone the expanded sidebar would take over half the screen, so there it rests as the icon
  // rail and expands *over* the content on demand. The workspace does the same at every width: it
  // is three resizable columns of tools, and the app nav is not what you are there to use — every
  // pixel it gives back goes to the editor. The stored preference still governs the other pages.
  const phone = useMediaQuery('(max-width: 767px)');
  const railMode = phone || workbench;
  const [overlayOpen, setOverlayOpen] = useState(false);
  const overlay = railMode && overlayOpen;
  const collapsed = railMode ? !overlayOpen : storedCollapsed;

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

  // Each route change fades the content in — the shell itself never moves.
  useEffect(() => {
    fadeInElement(mainRef.current);
    setOverlayOpen(false);
  }, [location.pathname]);

  // The phone overlay closes like any other overlay: Escape, or leaving phone width.
  useEffect(() => {
    if (!overlay) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setOverlayOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [overlay]);
  useEffect(() => {
    if (!railMode) setOverlayOpen(false);
  }, [railMode]);

  function logout(): void {
    clear();
    navigate('/login');
  }

  const initial = (user?.email ?? '?').charAt(0).toUpperCase();

  return (
    <div className="relative flex h-screen overflow-hidden bg-surface-canvas">
      {/* Holds the rail's place in the row while the phone overlay floats above the content. */}
      {railMode ? <div aria-hidden className="w-14 shrink-0" /> : null}
      {overlay ? (
        <div
          aria-hidden
          className="absolute inset-0 z-30 animate-ff-fade bg-black/40"
          onClick={() => setOverlayOpen(false)}
        />
      ) : null}
      <aside
        className={cn(
          'flex shrink-0 flex-col border-r border-edge bg-surface-canvas transition-[width] duration-200 ease-out',
          railMode ? 'absolute inset-y-0 left-0 z-40' : 'relative',
          overlay && 'shadow-pop',
          collapsed ? 'w-14' : 'w-52',
        )}
      >
        <div
          className={cn(
            'flex h-12 shrink-0 items-center gap-2.5',
            collapsed ? 'justify-center px-0' : 'px-4',
          )}
        >
          <BrandMark size={22} />
          {!collapsed ? (
            <span className="text-[13px] font-semibold tracking-tight text-fg">ForgeFlow</span>
          ) : null}
        </div>

        <nav className="flex-1 space-y-px px-2 py-2" aria-label="Main">
          {!collapsed ? (
            <p className="px-2.5 pb-1.5 pt-1 text-2xs font-medium text-fg-faint">Workspace</p>
          ) : null}
          {nav
            .filter((item) => !item.adminOnly || user?.role === 'admin')
            .map((item) => {
              const link = (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  title={collapsed ? undefined : item.label}
                  aria-label={collapsed ? item.label : undefined}
                  className={({ isActive }) =>
                    cn(
                      'relative flex h-8 w-full items-center gap-2.5 rounded-md px-2.5 text-[13px] transition-colors duration-150',
                      collapsed && 'justify-center px-0',
                      isActive ? 'text-fg' : 'text-fg-subtle hover:bg-fg/[0.04] hover:text-fg',
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive ? (
                        // One shared fill slides between items rather than blinking in per link.
                        <motion.span
                          layoutId="sidebar-active-pill"
                          aria-hidden
                          className="absolute inset-0 rounded-md bg-fg/[0.07]"
                          transition={{ duration: 0.22, ease: EASE }}
                        />
                      ) : null}
                      <item.icon
                        aria-hidden
                        className={cn(
                          'relative h-4 w-4 shrink-0',
                          isActive ? 'text-brand-text' : 'text-current',
                        )}
                        strokeWidth={1.75}
                      />
                      {!collapsed ? <span className="relative truncate">{item.label}</span> : null}
                    </>
                  )}
                </NavLink>
              );
              return collapsed ? (
                <Tooltip key={item.to} label={item.label} side="right" className="flex w-full">
                  {link}
                </Tooltip>
              ) : (
                link
              );
            })}
        </nav>

        {/* Build version — lets a user confirm an update actually took effect (phase-49). */}
        <div
          className={cn(
            'shrink-0 py-3 font-mono text-2xs text-fg-faint',
            collapsed ? 'text-center' : 'px-4',
          )}
          title={`ForgeFlow v${__APP_VERSION__}`}
          data-testid="app-version"
        >
          {collapsed ? `v` : `v${__APP_VERSION__}`}
        </div>
      </aside>

      <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="relative flex h-12 shrink-0 items-center justify-between gap-4 border-b border-edge px-3">
          <div className="flex min-w-0 items-center gap-1.5">
            <IconButton
              label="Toggle sidebar"
              size="md"
              tooltipAlign="start"
              aria-expanded={railMode ? overlayOpen : !storedCollapsed}
              onClick={() => (railMode ? setOverlayOpen((v) => !v) : toggleSidebar())}
            >
              <PanelLeft aria-hidden strokeWidth={1.75} />
            </IconButton>
            <button
              type="button"
              data-testid="command-trigger"
              onClick={() => setPaletteOpen(true)}
              className="hidden h-8 w-64 items-center gap-2 rounded-lg border border-edge bg-fg/[0.025] px-2.5 text-[13px] text-fg-faint transition-colors hover:border-edge-strong hover:text-fg-subtle md:flex"
            >
              <Search aria-hidden className="h-3.5 w-3.5 shrink-0" />
              <span className="flex-1 truncate text-left">Search or jump to…</span>
              <span className="flex items-center gap-0.5">
                <Kbd>{MOD_KEY}</Kbd>
                <Kbd>K</Kbd>
              </span>
            </button>
          </div>
          <div className="flex items-center gap-1.5 text-sm">
            <span
              className="mr-1 flex items-center gap-1.5 whitespace-nowrap px-1.5 text-xs text-fg-subtle"
              title="Realtime connection"
              data-testid="rt-status"
            >
              <span className={`h-1.5 w-1.5 rounded-full ${rtDot[rtStatus]}`} aria-hidden />
              {rtLabel[rtStatus]}
            </span>
            <ThemeToggle />
            <span className="mx-1 hidden h-5 w-px bg-edge sm:block" aria-hidden />
            <span className="hidden items-center gap-2 pl-1 sm:flex">
              <span
                aria-hidden
                className="flex h-6 w-6 items-center justify-center rounded-full bg-brand/20 text-2xs font-semibold text-brand-text"
              >
                {initial}
              </span>
              <span
                data-testid="current-user"
                className="max-w-[14rem] truncate text-[13px] text-fg-muted"
              >
                {user?.email}
              </span>
            </span>
            <button
              type="button"
              onClick={logout}
              aria-label="Log out"
              title="Log out"
              className="ml-1 inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-fg-subtle transition-colors hover:bg-surface-raised hover:text-fg"
            >
              <LogOut aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
              <span className="hidden sm:inline">Log out</span>
            </button>
          </div>
        </header>

        <main
          data-app-scroll
          className={cn(
            'relative min-h-0 flex-1 overflow-auto',
            workbench ? 'p-2' : 'p-4 sm:p-6 lg:p-8',
          )}
        >
          <div ref={mainRef} className="h-full">
            <Outlet />
          </div>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
