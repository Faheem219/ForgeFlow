import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAuthStore } from '../../lib/stores/authStore';
import { Terminal } from './Terminal';

// xterm needs real layout/canvas measurement, which jsdom lacks — mock the terminal itself and
// assert on the wiring (mount, data in, keystrokes out, resize).
const term = {
  cols: 80,
  rows: 24,
  // The live component restyles via `term.options.theme = …` on theme switches.
  options: {} as Record<string, unknown>,
  loadAddon: vi.fn(),
  open: vi.fn(),
  write: vi.fn(),
  dispose: vi.fn(),
  onData: vi.fn((cb: (d: string) => void) => {
    term.__onData = cb;
    return { dispose: vi.fn() };
  }),
  __onData: undefined as ((d: string) => void) | undefined,
};
const fit = { fit: vi.fn() };

vi.mock('@xterm/xterm', () => ({ Terminal: vi.fn(() => term) }));
vi.mock('@xterm/addon-fit', () => ({ FitAddon: vi.fn(() => fit) }));
vi.mock('@xterm/xterm/css/xterm.css', () => ({}));

class MockWebSocket {
  static OPEN = 1;
  static instances: MockWebSocket[] = [];
  url: string;
  readyState = 0;
  binaryType = '';
  sent: unknown[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }
  send(data: unknown): void {
    this.sent.push(data);
  }
  close(): void {
    this.readyState = 3;
    this.onclose?.({} as CloseEvent);
  }
  emitOpen(): void {
    this.readyState = 1;
    this.onopen?.({} as Event);
  }
  emitBinary(text: string): void {
    this.onmessage?.({ data: new TextEncoder().encode(text).buffer } as MessageEvent);
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe(): void {}
      disconnect(): void {}
    },
  );
  // The component defers xterm construction by one animation frame (so the renderer never
  // measures a mid-transition zero-size host) — run it synchronously so existing assertions
  // right after render() still see the terminal mounted.
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
  vi.stubGlobal('cancelAnimationFrame', () => {});
  useAuthStore.setState({
    token: 'tok',
    user: { id: 'u1', email: 'a@b.c', role: 'user', created_at: '2026-01-01T00:00:00Z' },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe('Terminal', () => {
  it('mounts xterm into the host element and connects', () => {
    render(<Terminal projectId="p1" />);

    expect(term.open).toHaveBeenCalledWith(screen.getByTestId('terminal-host'));
    expect(term.loadAddon).toHaveBeenCalledWith(fit);
    expect(fit.fit).toHaveBeenCalled();
    expect(MockWebSocket.instances[0].url).toContain('/ws/projects/p1/terminal');
  });

  it('writes incoming socket bytes to the terminal', async () => {
    render(<Terminal projectId="p1" />);
    const ws = MockWebSocket.instances[0];
    ws.emitOpen();
    ws.emitBinary('user@sandbox:/workspace$ ');

    await waitFor(() => expect(term.write).toHaveBeenCalledWith('user@sandbox:/workspace$ '));
  });

  it('forwards keystrokes from the terminal to the socket', () => {
    render(<Terminal projectId="p1" />);
    const ws = MockWebSocket.instances[0];
    ws.emitOpen();

    term.__onData?.('node -v\r');
    expect(new TextDecoder().decode(ws.sent[0] as Uint8Array)).toBe('node -v\r');
  });

  it('shows the connection status', async () => {
    render(<Terminal projectId="p1" />);
    expect(screen.getByTestId('terminal-status')).toHaveTextContent('connecting');

    MockWebSocket.instances[0].emitOpen();
    await waitFor(() =>
      expect(screen.getByTestId('terminal-status')).toHaveTextContent('connected'),
    );
  });

  it('tears the shell down on unmount', () => {
    const { unmount } = render(<Terminal projectId="p1" />);
    const ws = MockWebSocket.instances[0];
    ws.emitOpen();

    unmount();
    expect(term.dispose).toHaveBeenCalled();
    expect(ws.readyState).toBe(3); // socket closed → backend reaps the PTY
  });
});
