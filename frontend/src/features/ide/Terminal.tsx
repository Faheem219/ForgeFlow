import { FitAddon } from '@xterm/addon-fit';
import { Terminal as XTerm, type ITheme } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { SquareTerminal } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { dark, light, type ResolvedTheme } from '../../app/theme/palette';
import { cn } from '../../lib/cn';
import { useAuthStore } from '../../lib/stores/authStore';
import { useThemeStore } from '../../lib/stores/themeStore';
import { TerminalClient, type TerminalStatus } from './terminalClient';

// xterm renders to canvas and cannot read CSS custom properties, so both themes are built from
// palette.ts. The light ANSI ramp is retuned by hand: the standard bright set (yellow #ffff00,
// green #00ff00…) is unreadable on white, and tool output leans on exactly those colors.
const THEMES: Record<ResolvedTheme, ITheme> = {
  // The dark ramp is retuned too: xterm's stock ANSI set (#cd3131 red, #0dbc79 green…) is far
  // more saturated than anything else on screen, so build logs shouted over the editor beside them.
  dark: {
    background: dark.surface,
    foreground: dark.fg,
    cursor: dark.brandText,
    cursorAccent: dark.surface,
    selectionBackground: '#5b5bd655',
    black: '#2a282e',
    red: '#f2817f',
    green: '#7cc896',
    yellow: '#e5b450',
    blue: '#7ea8f0',
    magenta: '#b6a8f5',
    cyan: '#7cc4d4',
    white: '#b4b1bb',
    brightBlack: '#6e6b76',
    brightRed: '#f6a19f',
    brightGreen: '#9fd3a8',
    brightYellow: '#efca7c',
    brightBlue: '#a2c0f5',
    brightMagenta: '#cdc2fa',
    brightCyan: '#a3d7e2',
    brightWhite: '#ecebef',
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
    let cancelled = false;
    const create = (): void => {
      if (cancelled) return;
      const term = new XTerm({
        convertEol: false,
        fontSize: 12.5,
        lineHeight: 1.3,
        fontFamily: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace',
        theme: THEMES[useThemeStore.getState().resolved],
        cursorBlink: true,
        cursorStyle: 'bar',
        scrollback: 5000,
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
    };

    const rafId = requestAnimationFrame(() => {
      // xterm measures its cell size once, from whatever font is live at construction, and does
      // not re-measure if the same family later finishes loading — so a terminal opened before the
      // mono webfont arrived would keep the fallback's metrics. Wait for it when it is not ready.
      const fonts = typeof document !== 'undefined' ? document.fonts : undefined;
      const spec = '12.5px "JetBrains Mono"';
      if (fonts && typeof fonts.check === 'function' && !fonts.check(spec)) {
        void fonts
          .load(spec)
          .catch(() => undefined)
          .finally(create);
      } else {
        create();
      }
    });

    return () => {
      cancelled = true;
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
      <div className="flex h-8 shrink-0 items-center justify-between border-b border-edge px-3">
        <span className="flex items-center gap-1.5 text-xs font-medium text-fg-muted">
          <SquareTerminal aria-hidden className="h-3.5 w-3.5 text-fg-subtle" strokeWidth={1.75} />
          Terminal
          <span className="font-normal text-fg-faint">· sandbox</span>
        </span>
        <span className="flex items-center gap-1.5 text-2xs text-fg-subtle">
          <span
            aria-hidden
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              status === 'open'
                ? 'bg-success'
                : status === 'connecting'
                  ? 'bg-warning motion-safe:animate-ff-breathe'
                  : 'bg-fg-faint',
            )}
          />
          <span data-testid="terminal-status">{statusLabel[status]}</span>
        </span>
      </div>
      {/* The padding lives on a wrapper: xterm's fit addon measures the host's *content* box, so
          padding on the host itself would be counted as columns it cannot draw into. */}
      <div className="min-h-0 flex-1 px-3 pb-1 pt-2">
        <div ref={hostRef} data-testid="terminal-host" className="h-full overflow-hidden" />
      </div>
    </div>
  );
}

export default Terminal;
