import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Kbd } from '../../components/ui';
import { IconDashboard, IconEval, IconSearch, IconSettings } from '../../components/ui/Icons';
import { useAuthStore } from '../../lib/stores/authStore';
import { useThemeStore } from '../../lib/stores/themeStore';
import { useUiStore } from '../../lib/stores/uiStore';
import { listProjects } from '../dashboard/api';
import { STAGE_LABELS } from '../workspace/stageMeta';
import { subsequenceMatch } from './match';

/**
 * ⌘K / Ctrl-K palette.
 *
 * The keyboard entry point a developer tool is expected to have. Two design calls:
 *  - **Projects are commands.** Jumping to a project is the most common navigation in this app, so
 *    they are listed alongside actions rather than hidden behind a mode switch.
 *  - **Subsequence matching**, not substring: "dsh" finds "Dashboard", the way every editor's
 *    fuzzy-open behaves. Anything stricter feels broken to the audience this is aimed at.
 */

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon?: JSX.Element;
  run: () => void;
}

export function CommandPalette(): JSX.Element | null {
  const open = useUiStore((s) => s.commandOpen);
  const setOpen = useUiStore((s) => s.setCommandOpen);
  const toggleSidebar = useUiStore((s) => s.toggleSidebar);
  const resetPanelSizes = useUiStore((s) => s.resetPanelSizes);
  const setMode = useThemeStore((s) => s.setMode);
  const user = useAuthStore((s) => s.user);
  const clear = useAuthStore((s) => s.clear);
  const navigate = useNavigate();

  const [query, setQuery] = useState('');
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // Projects are only fetched while the palette is open — it is not worth a request otherwise.
  const projectsQuery = useQuery({ queryKey: ['projects'], queryFn: listProjects, enabled: open });

  // Global shortcut.
  useEffect(() => {
    function onKey(event: KeyboardEvent): void {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen(!useUiStore.getState().commandOpen);
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setOpen]);

  useEffect(() => {
    if (open) {
      setQuery('');
      setCursor(0);
      // Focus after paint, else the input is not yet in the document.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const commands = useMemo<Command[]>(() => {
    const close = (fn: () => void) => () => {
      setOpen(false);
      fn();
    };
    const nav: Command[] = [
      {
        id: 'nav-dashboard',
        label: 'Go to Projects',
        group: 'Navigate',
        icon: <IconDashboard size={14} />,
        run: close(() => navigate('/')),
      },
      {
        id: 'nav-eval',
        label: 'Go to Evaluation',
        group: 'Navigate',
        icon: <IconEval size={14} />,
        run: close(() => navigate('/eval')),
      },
      {
        id: 'nav-settings',
        label: 'Go to Settings',
        group: 'Navigate',
        icon: <IconSettings size={14} />,
        run: close(() => navigate('/settings')),
      },
    ];

    const projects: Command[] = (projectsQuery.data ?? []).map((p) => ({
      id: `project-${p.id}`,
      label: p.name,
      hint: STAGE_LABELS[p.current_stage],
      group: 'Projects',
      run: close(() => navigate(`/projects/${p.id}`)),
    }));

    const actions: Command[] = [
      { id: 'theme-light', label: 'Theme: Light', group: 'Preferences', run: close(() => setMode('light')) },
      { id: 'theme-dark', label: 'Theme: Dark', group: 'Preferences', run: close(() => setMode('dark')) },
      {
        id: 'theme-system',
        label: 'Theme: Match system',
        group: 'Preferences',
        run: close(() => setMode('system')),
      },
      {
        id: 'toggle-sidebar',
        label: 'Toggle sidebar',
        group: 'Preferences',
        run: close(toggleSidebar),
      },
      {
        id: 'reset-panels',
        label: 'Reset panel layout',
        hint: 'Restore every splitter to its default size',
        group: 'Preferences',
        run: close(resetPanelSizes),
      },
      {
        id: 'logout',
        label: 'Log out',
        hint: user?.email,
        group: 'Account',
        run: close(() => {
          clear();
          navigate('/login');
        }),
      },
    ];

    return [...nav, ...projects, ...actions];
  }, [projectsQuery.data, navigate, setOpen, setMode, toggleSidebar, resetPanelSizes, clear, user]);

  const matches = useMemo(
    () => commands.filter((c) => subsequenceMatch(`${c.label} ${c.hint ?? ''}`, query)),
    [commands, query],
  );

  // Keep the cursor inside the (shrinking) result set as the user types.
  useEffect(() => setCursor(0), [query]);

  useEffect(() => {
    listRef.current
      ?.querySelector('[data-active="true"]')
      ?.scrollIntoView({ block: 'nearest' });
  }, [cursor, matches.length]);

  if (!open) return null;

  function onKeyDown(event: React.KeyboardEvent): void {
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      setCursor((c) => (matches.length === 0 ? 0 : (c + 1) % matches.length));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setCursor((c) => (matches.length === 0 ? 0 : (c - 1 + matches.length) % matches.length));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      matches[cursor]?.run();
    }
  }

  let lastGroup = '';

  return (
    <div className="fixed inset-0 z-[70] flex items-start justify-center p-4 pt-[12vh]">
      <div
        className="absolute inset-0 animate-fade-in bg-scrim"
        style={{ opacity: 'var(--scrim-opacity)' }}
        onClick={() => setOpen(false)}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onKeyDown={onKeyDown}
        className="on-surface relative w-full max-w-lg animate-slide-up overflow-hidden rounded-xl border border-line bg-overlay shadow-overlay"
      >
        <div className="flex items-center gap-2 border-b border-subtle px-3">
          <IconSearch size={15} className="shrink-0 text-faint" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search projects and commands…"
            aria-label="Search projects and commands"
            data-testid="command-input"
            className="h-11 flex-1 bg-transparent text-sm text-primary outline-none placeholder:text-faint"
          />
          <Kbd>esc</Kbd>
        </div>

        <div ref={listRef} className="max-h-[52vh] overflow-y-auto p-1.5" role="listbox">
          {matches.length === 0 ? (
            <p className="px-2.5 py-6 text-center text-sm text-muted">No matches.</p>
          ) : (
            matches.map((command, i) => {
              const showGroup = command.group !== lastGroup;
              lastGroup = command.group;
              const active = i === cursor;
              return (
                <div key={command.id}>
                  {showGroup ? <p className="eyebrow px-2.5 pb-1 pt-2.5">{command.group}</p> : null}
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    data-active={active}
                    data-testid={`command-${command.id}`}
                    onClick={command.run}
                    onMouseMove={() => setCursor(i)}
                    className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-1.5 text-left text-sm transition-colors duration-instant ease-out ${
                      active ? 'bg-brand-bg text-primary' : 'text-secondary'
                    }`}
                  >
                    {command.icon ? <span className="shrink-0 text-faint">{command.icon}</span> : null}
                    <span className="min-w-0 flex-1 truncate">{command.label}</span>
                    {command.hint ? (
                      <span className="shrink-0 truncate text-2xs text-faint">{command.hint}</span>
                    ) : null}
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

export default CommandPalette;
