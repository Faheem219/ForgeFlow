import { FitAddon } from '@xterm/addon-fit';
import { Terminal as XTerm } from '@xterm/xterm';
import '@xterm/xterm/css/xterm.css';
import { useEffect, useRef, useState } from 'react';

import { useAuthStore } from '../../lib/stores/authStore';
import { TerminalClient, type TerminalStatus } from './terminalClient';

const theme = {
  background: '#0f172a', // slate-900, matches the workspace panels
  foreground: '#e2e8f0',
  cursor: '#a5b4fc',
  selectionBackground: '#334155',
};

const statusLabel: Record<TerminalStatus, string> = {
  open: 'connected',
  connecting: 'connecting',
  closed: 'disconnected',
};

export function Terminal({ projectId }: { projectId: string }): JSX.Element {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const token = useAuthStore((s) => s.token);
  const [status, setStatus] = useState<TerminalStatus>('closed');

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !token) return;

    const term = new XTerm({ convertEol: false, fontSize: 13, theme, cursorBlink: true });
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

    return () => {
      observer.disconnect();
      keyed.dispose();
      offData();
      offStatus();
      client.close();
      term.dispose();
    };
  }, [projectId, token]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-slate-800 px-3 py-1.5">
        <span className="text-xs font-medium text-slate-300">Terminal</span>
        <span className="text-xs text-slate-500" data-testid="terminal-status">
          {statusLabel[status]}
        </span>
      </div>
      <div ref={hostRef} data-testid="terminal-host" className="min-h-0 flex-1 overflow-hidden" />
    </div>
  );
}

export default Terminal;
