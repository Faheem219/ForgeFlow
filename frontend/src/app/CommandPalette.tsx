import { useQuery } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import {
  ChartNoAxesColumn,
  FolderKanban,
  LayoutGrid,
  LogOut,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  SunMoon,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { useNavigate } from 'react-router-dom';

import { Kbd } from '../components/ui';
import { cn } from '../lib/cn';
import { listProjects } from '../features/dashboard/api';
import { DURATION, EASE } from '../lib/motion';
import { useAuthStore } from '../lib/stores/authStore';
import { useThemeStore, type ThemeMode } from '../lib/stores/themeStore';
import { useUiStore } from '../lib/stores/uiStore';

interface Command {
  id: string;
  section: 'Projects' | 'Go to' | 'Actions';
  label: string;
  icon: LucideIcon;
  hint?: string;
  run: () => void;
}

const THEME_CYCLE: ThemeMode[] = ['dark', 'light', 'system'];

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}): JSX.Element {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.user?.role);
  const clearAuth = useAuthStore((s) => s.clear);
  const themeMode = useThemeStore((s) => s.mode);
  const setThemeMode = useThemeStore((s) => s.setMode);
  const requestNewProject = useUiStore((s) => s.requestNewProject);

  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);

  // Shares the dashboard's cache key, so opening the palette is usually instant.
  const projectsQuery = useQuery({
    queryKey: ['projects'],
    queryFn: listProjects,
    enabled: open,
  });

  useEffect(() => {
    if (open) {
      setQuery('');
      setSelected(0);
    }
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const go = (to: string) => () => {
      onClose();
      navigate(to);
    };
    const items: Command[] = [
      ...(projectsQuery.data ?? []).map((p): Command => ({
        id: `project-${p.id}`,
        section: 'Projects',
        label: p.name,
        icon: FolderKanban,
        hint: 'Open workspace',
        run: go(`/projects/${p.id}`),
      })),
      {
        id: 'nav-dashboard',
        section: 'Go to',
        label: 'Dashboard',
        icon: LayoutGrid,
        run: go('/dashboard'),
      },
      {
        id: 'nav-eval',
        section: 'Go to',
        label: 'Evaluation',
        icon: ChartNoAxesColumn,
        run: go('/eval'),
      },
      {
        id: 'nav-settings',
        section: 'Go to',
        label: 'Settings',
        icon: Settings2,
        run: go('/settings'),
      },
      {
        id: 'action-new-project',
        section: 'Actions',
        label: 'New project',
        icon: Plus,
        run: () => {
          onClose();
          navigate('/dashboard');
          requestNewProject();
        },
      },
      {
        id: 'action-theme',
        section: 'Actions',
        label: 'Cycle theme',
        icon: SunMoon,
        hint: themeMode,
        run: () => {
          setThemeMode(THEME_CYCLE[(THEME_CYCLE.indexOf(themeMode) + 1) % THEME_CYCLE.length]);
        },
      },
      {
        id: 'action-logout',
        section: 'Actions',
        label: 'Log out',
        icon: LogOut,
        run: () => {
          onClose();
          clearAuth();
          navigate('/login');
        },
      },
    ];
    if (role === 'admin') {
      items.splice(items.length - 3, 0, {
        id: 'nav-admin',
        section: 'Go to',
        label: 'Admin',
        icon: ShieldCheck,
        run: go('/admin'),
      });
    }
    return items;
  }, [
    projectsQuery.data,
    role,
    themeMode,
    navigate,
    onClose,
    clearAuth,
    setThemeMode,
    requestNewProject,
  ]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => c.label.toLowerCase().includes(q));
  }, [commands, query]);

  // Selection stays valid as the filter narrows.
  useEffect(() => {
    setSelected((s) => Math.min(s, Math.max(filtered.length - 1, 0)));
  }, [filtered.length]);

  useEffect(() => {
    document.getElementById(`cmd-item-${selected}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [selected]);

  function onKeyDown(event: React.KeyboardEvent): void {
    if (event.key === 'Escape') {
      onClose();
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      setSelected((s) => (filtered.length ? (s + 1) % filtered.length : 0));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setSelected((s) => (filtered.length ? (s - 1 + filtered.length) % filtered.length : 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      filtered[selected]?.run();
    }
  }

  // Render grouped, but navigate flat — each row knows its index in `filtered`.
  const sections: Array<Command['section']> = ['Projects', 'Go to', 'Actions'];

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-50" data-testid="command-palette">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: DURATION.fast }}
            className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
            onClick={onClose}
            aria-hidden
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
            initial={{ opacity: 0, scale: 0.97, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: -6 }}
            transition={{ duration: DURATION.base, ease: EASE }}
            className="relative mx-auto mt-[12vh] w-full max-w-lg overflow-hidden rounded-2xl border border-edge-strong bg-surface-overlay shadow-2xl"
            onKeyDown={onKeyDown}
          >
            <div className="flex items-center gap-2.5 border-b border-edge px-4">
              <Search aria-hidden className="h-4 w-4 shrink-0 text-fg-subtle" />
              <input
                autoFocus
                aria-label="Search commands and projects"
                data-testid="command-input"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search projects, pages, actions…"
                className="h-12 flex-1 bg-transparent text-sm text-fg placeholder:text-fg-faint outline-none"
              />
              <Kbd>Esc</Kbd>
            </div>

            <div role="listbox" aria-label="Results" className="max-h-[46vh] overflow-y-auto p-1.5">
              {filtered.length === 0 ? (
                <p className="px-3 py-8 text-center text-sm text-fg-subtle">
                  Nothing matches “{query}”.
                </p>
              ) : (
                sections.map((section) => {
                  const rows = filtered.filter((c) => c.section === section);
                  if (rows.length === 0) return null;
                  return (
                    <div key={section}>
                      <p className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-fg-faint">
                        {section}
                      </p>
                      {rows.map((cmd) => {
                        const index = filtered.indexOf(cmd);
                        return (
                          <button
                            key={cmd.id}
                            id={`cmd-item-${index}`}
                            type="button"
                            role="option"
                            aria-selected={index === selected}
                            data-testid={`cmd-${cmd.id}`}
                            onClick={() => cmd.run()}
                            onPointerEnter={() => setSelected(index)}
                            className={cn(
                              'flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition-colors',
                              index === selected
                                ? 'bg-brand/10 text-fg'
                                : 'text-fg-muted hover:bg-surface-raised',
                            )}
                          >
                            <cmd.icon
                              aria-hidden
                              className={cn(
                                'h-4 w-4 shrink-0',
                                index === selected ? 'text-brand-text' : 'text-fg-subtle',
                              )}
                              strokeWidth={1.75}
                            />
                            <span className="min-w-0 flex-1 truncate">{cmd.label}</span>
                            {cmd.hint ? (
                              <span className="shrink-0 text-xs text-fg-faint">{cmd.hint}</span>
                            ) : null}
                          </button>
                        );
                      })}
                    </div>
                  );
                })
              )}
            </div>
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
