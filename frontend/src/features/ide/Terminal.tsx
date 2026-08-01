import { FitAddon } from '@xterm/addon-fit';
import { Terminal as XTerm } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { useEffect, useRef, useState } from 'react';

import { useAuthStore } from '../../lib/stores/authStore';
import { useThemeStore, type ResolvedTheme } from '../../lib/stores/themeStore';
import { TerminalClient, type TerminalStatus } from './terminalClient';

/**
 * xterm renders to a canvas, so — like Monaco — it cannot inherit CSS variables and needs the
 * palette handed to it. Values mirror `src/app/theme/tokens.css`.
 *
 * The ANSI colours are tuned per theme rather than shared: the standard bright ANSI set is designed
 * for a dark background and turns to mud on white (yellow especially). A build log the user cannot
 * read in light mode would make light mode useless for the stage that matters most.
 */
const THEMES: Record<ResolvedTheme, Record<string, string>> = {
  dark: {
    background: '#0b0f18', // --surface
    foreground: '#c2c9d6', // --text-secondary
    cursor: '#818cf8', // --brand-hover
    cursorAccent: '#0b0f18',
    selectionBackground: '#262f40', // --border-default
    black: '#141a26',
    red: '#fb7185',
    green: '#34d399',
    yellow: '#fbbf24',
    blue: '#60a5fa',
    magenta: '#a78bfa',
    cyan: '#38bdf8',
    white: '#edf0f6',
    brightBlack: '#5f6779',
    brightRed: '#fda4af',
    brightGreen: '#6ee7b7',
    brightYellow: '#fcd34d',
    brightBlue: '#93c5fd',
    brightMagenta: '#c4b5fd',
    brightCyan: '#7dd3fc',
    brightWhite: '#ffffff',
  },
  light: {
    background: '#ffffff', // --surface
    foreground: '#3b4354', // --text-secondary
    cursor: '#4f46e5', // --brand
    cursorAccent: '#ffffff',
    selectionBackground: '#dbe0ff',
    black: '#0b0f18',
    red: '#be123c',
    green: '#057a55',
    yellow: '#b45309',
    blue: '#1d4ed8',
    magenta: '#7c3aed',
    cyan: '#0369a1',
    white: '#626b7d',
    brightBlack: '#8a92a1',
    brightRed: '#e11d48',
    brightGreen: '#059669',
    brightYellow: '#c2410c',
    brightBlue: '#2563eb',
    brightMagenta: '#9333ea',
    brightCyan: '#0284c7',
    brightWhite: '#0b0f18',
  },
};

const statusLabel: Record<TerminalStatus, string> = {
  open: 'connected',
  connecting: 'connecting',
  closed: 'disconnected',
};

const statusDot: Record<TerminalStatus, string> = {
  open: 'bg-success',
  connecting: 'bg-warning motion-essential animate-pulse-dot',
  closed: 'bg-faint',
};

export function Terminal({ projectId }: { projectId: string }): JSX.Element {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const token = useAuthStore((s) => s.token);
  const resolved = useThemeStore((s) => s.resolved);
  const [status, setStatus] = useState<TerminalStatus>('closed');

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !token) return;

    const term = new XTerm({
      convertEol: false,
      fontSize: 12.5,
      lineHeight: 1.35,
      fontFamily: 'var(--font-mono)',
      theme: THEMES[useThemeStore.getState().resolved],
      cursorBlink: true,
      // A generous scrollback: a failing install or test run is exactly what the user scrolls up
      // to read, and truncating it defeats the point of having a terminal at all.
      scrollback: 5000,
    });
    termRef.current = term;
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    fit.fit();

    const client = new TerminalClient(projectId, token);
    const decoder = new TextDecoder();

    const offData = client.onData((bytes) => term.write(decoder.decode(bytes)));
    const offStatus = client.onStatus(setStatus);
    // Keystrokes go straight through as bytes — the sandbox PTY interprets them.
    const keyed = term.onData((data) => client.send(data));

    client.connect(term.cols, term.rows);

    // Keep the PTY's window size in step with the rendered element. This also fires while a
    // splitter is dragged, which is what makes the terminal resize live rather than on release.
    const observer = new ResizeObserver(() => {
      try {
        fit.fit();
        client.resize(term.cols, term.rows);
      } catch {
        // fit throws if the element is momentarily unmeasurable (hidden/unmounting).
      }
    });
    observer.observe(host);

    return () => {
      observer.disconnect();
      keyed.dispose();
      offData();
      offStatus();
      client.close();
      term.dispose();
      termRef.current = null;
    };
  }, [projectId, token]);

  // Repaint on theme change without tearing down the session — losing scrollback (and any running
  // process's output) just because someone toggled the theme would be a poor trade.
  useEffect(() => {
    if (termRef.current) termRef.current.options.theme = THEMES[resolved];
  }, [resolved]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-subtle px-3 py-1.5">
        <span className="eyebrow">Terminal</span>
        <span
          className="flex items-center gap-1.5 text-2xs text-muted"
          data-testid="terminal-status"
        >
          <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${statusDot[status]}`} />
          {statusLabel[status]}
        </span>
      </div>
      <div
        ref={hostRef}
        data-testid="terminal-host"
        className="min-h-0 flex-1 overflow-hidden px-2 py-1"
      />
    </div>
  );
}

export default Terminal;
