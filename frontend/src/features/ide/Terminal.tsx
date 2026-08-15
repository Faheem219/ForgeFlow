import { FitAddon } from '@xterm/addon-fit';
import { Terminal as XTerm, type ITheme } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { useEffect, useRef, useState } from 'react';

import { dark, light, type ResolvedTheme } from '../../app/theme/palette';
import { useAuthStore } from '../../lib/stores/authStore';
import { useThemeStore } from '../../lib/stores/themeStore';
import { TerminalClient, type TerminalStatus } from './terminalClient';

// xterm renders to canvas and cannot read CSS custom properties, so both themes are built from
// palette.ts. The light ANSI ramp is retuned by hand: the standard bright set (yellow #ffff00,
// green #00ff00…) is unreadable on white, and tool output leans on exactly those colors.
const THEMES: Record<ResolvedTheme, ITheme> = {
  dark: {
    background: dark.sunken,
    foreground: dark.fg,
    cursor: dark.brandText,
    selectionBackground: dark.edgeStrong,
  },
  light: {
    background: light.surface,
    foreground: light.fg,
    cursor: light.brand,
    selectionBackground: light.edge,
    black: '#17171c',
    red: '#b91c1c',
    green: '#047857',
    yellow: '#b45309',
    blue: '#2563eb',
    magenta: '#7c3aed',
    cyan: '#0e7490',
    white: '#6e6e7a',
    brightBlack: '#4c4c58',
    brightRed: '#dc2626',
    brightGreen: '#059669',
    brightYellow: '#d97706',
    brightBlue: '#3b82f6',
    brightMagenta: '#8b5cf6',
    brightCyan: '#0891b2',
    brightWhite: '#17171c',
  },
};

const statusLabel: Record<TerminalStatus, string> = {
  open: 'connected',
  connecting: 'connecting',
  closed: 'disconnected',
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

    let cleanup: (() => void) | null = null;

    // Deferring construction to the next animation frame guarantees a layout pass has run, so
    // xterm's renderer never measures the host mid-transition (tab switch, drawer open). Without
    // this, xterm can schedule its own internal viewport sync against a zero-size box; that sync
    // runs on a later frame outside our control, so wrapping just fit.fit() below can't catch it
    // — it surfaces as an uncaught "Cannot read properties of undefined (reading 'dimensions')"
    // the next time the terminal panel is reopened.
    const rafId = requestAnimationFrame(() => {
      const term = new XTerm({
        convertEol: false,
        fontSize: 13,
        theme: THEMES[useThemeStore.getState().resolved],
        cursorBlink: true,
      });
      termRef.current = term;
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(host);
      try {
        fit.fit();
      } catch {
        // The host can still have a zero/unmeasurable box on the very first paint (drawer
        // transition, Suspense boundary just resolved) — the ResizeObserver below re-fits once
        // layout settles, so a failure here is harmless.
      }

      const client = new TerminalClient(projectId, token);
      const decoder = new TextDecoder();

      const offData = client.onData((bytes) => term.write(decoder.decode(bytes)));
      const offStatus = client.onStatus(setStatus);
      // Keystrokes go straight through as bytes — the sandbox PTY interprets them.
      const keyed = term.onData((data) => client.send(data));

      client.connect(term.cols, term.rows);

      // Keep the PTY's window size in step with the rendered element.
      const observer = new ResizeObserver(() => {
        try {
          fit.fit();
          client.resize(term.cols, term.rows);
        } catch {
          // fit throws if the element is momentarily unmeasurable (hidden/unmounting).
        }
      });
      observer.observe(host);

      cleanup = () => {
        observer.disconnect();
        keyed.dispose();
        offData();
        offStatus();
        client.close();
        term.dispose();
        termRef.current = null;
      };
    });

    return () => {
      cancelAnimationFrame(rafId);
      cleanup?.();
    };
  }, [projectId, token]);

  // Theme switches restyle the LIVE terminal (xterm applies option changes immediately) rather
  // than recreating it — recreation would wipe the scrollback and any running session's output.
  useEffect(() => {
    const term = termRef.current;
    if (term) term.options.theme = THEMES[resolved];
  }, [resolved]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-edge px-3 py-1.5">
        <span className="text-xs font-medium text-fg-muted">Terminal</span>
        <span className="text-xs text-fg-subtle" data-testid="terminal-status">
          {statusLabel[status]}
        </span>
      </div>
      <div ref={hostRef} data-testid="terminal-host" className="min-h-0 flex-1 overflow-hidden" />
    </div>
  );
}

export default Terminal;
