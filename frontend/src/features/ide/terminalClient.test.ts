import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TerminalClient } from './terminalClient';

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
    const buf = new TextEncoder().encode(text).buffer;
    this.onmessage?.({ data: buf } as MessageEvent);
  }
}

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('TerminalClient', () => {
  it('connects to the terminal endpoint carrying size and token', () => {
    const client = new TerminalClient('p1', 'tok', 'ws://test');
    client.connect(120, 40);

    const ws = MockWebSocket.instances[0];
    expect(ws.url).toContain('/ws/projects/p1/terminal');
    expect(ws.url).toContain('token=tok');
    expect(ws.url).toContain('cols=120');
    expect(ws.url).toContain('rows=40');
    expect(ws.binaryType).toBe('arraybuffer');
  });

  it('decodes incoming binary frames to bytes for the terminal', () => {
    const client = new TerminalClient('p1', 'tok', 'ws://test');
    const received: string[] = [];
    const decoder = new TextDecoder();
    client.onData((bytes) => received.push(decoder.decode(bytes)));
    client.connect();

    const ws = MockWebSocket.instances[0];
    ws.emitOpen();
    ws.emitBinary('hello$ ');

    expect(received).toEqual(['hello$ ']);
  });

  it('sends keystrokes as binary once open', () => {
    const client = new TerminalClient('p1', 'tok', 'ws://test');
    client.connect();
    const ws = MockWebSocket.instances[0];

    client.send('ls'); // dropped: not open yet
    expect(ws.sent).toHaveLength(0);

    ws.emitOpen();
    client.send('ls\r');
    expect(ws.sent).toHaveLength(1);
    expect(new TextDecoder().decode(ws.sent[0] as Uint8Array)).toBe('ls\r');
  });

  it('sends Ctrl-C through as a raw 0x03 byte', () => {
    const client = new TerminalClient('p1', 'tok', 'ws://test');
    client.connect();
    const ws = MockWebSocket.instances[0];
    ws.emitOpen();

    client.send('\x03');
    expect(new Uint8Array(ws.sent[0] as Uint8Array)[0]).toBe(3);
  });

  it('sends resize as a JSON control frame', () => {
    const client = new TerminalClient('p1', 'tok', 'ws://test');
    client.connect();
    const ws = MockWebSocket.instances[0];
    ws.emitOpen();

    client.resize(100, 30);
    expect(JSON.parse(ws.sent[0] as string)).toEqual({ type: 'resize', cols: 100, rows: 30 });
  });

  it('reconnects with the latest size after a drop', () => {
    const client = new TerminalClient('p1', 'tok', 'ws://test');
    client.connect(80, 24);
    const ws = MockWebSocket.instances[0];
    ws.emitOpen();
    client.resize(133, 44);

    ws.close();
    expect(MockWebSocket.instances).toHaveLength(1); // scheduled, not immediate
    vi.advanceTimersByTime(500);
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1].url).toContain('cols=133');
    expect(MockWebSocket.instances[1].url).toContain('rows=44');
  });

  it('stops reconnecting after close()', () => {
    const client = new TerminalClient('p1', 'tok', 'ws://test');
    client.connect();
    client.close();
    vi.advanceTimersByTime(5000);
    expect(MockWebSocket.instances).toHaveLength(1);
  });

  it('reports status transitions', () => {
    const client = new TerminalClient('p1', 'tok', 'ws://test');
    const seen: string[] = [];
    client.onStatus((s) => seen.push(s));
    client.connect();
    MockWebSocket.instances[0].emitOpen();

    expect(seen).toEqual(['closed', 'connecting', 'open']);
  });
});
