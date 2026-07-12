import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { RealtimeClient, type RealtimeEvent } from './wsClient';

class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  readyState = 0;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close(): void {
    this.readyState = 3;
    this.onclose?.({} as CloseEvent);
  }

  emitOpen(): void {
    this.readyState = 1;
    this.onopen?.({} as Event);
  }

  emitMessage(data: unknown): void {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

function makeEvent(seq: number, event = 'progress'): RealtimeEvent {
  return { event, seq, project_id: 'demo-1', stage: null, payload: {}, ts: '' };
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

describe('RealtimeClient', () => {
  it('dedupes by seq and ignores pings', () => {
    const client = new RealtimeClient('demo-1', 'tok', 'ws://test');
    const received: number[] = [];
    client.onEvent((e) => received.push(e.seq));
    client.connect();

    const ws = MockWebSocket.instances[0];
    expect(ws.url).toContain('/ws/projects/demo-1');
    expect(ws.url).toContain('last_seq=0');

    ws.emitOpen();
    ws.emitMessage(makeEvent(1));
    ws.emitMessage(makeEvent(1)); // duplicate seq
    ws.emitMessage({
      event: 'ping',
      seq: 0,
      project_id: 'demo-1',
      stage: null,
      payload: {},
      ts: '',
    });
    ws.emitMessage(makeEvent(2));

    expect(received).toEqual([1, 2]);
  });

  it('reconnects after a drop and resumes from last_seq', () => {
    const client = new RealtimeClient('demo-1', 'tok', 'ws://test');
    client.connect();

    const ws1 = MockWebSocket.instances[0];
    ws1.emitOpen();
    ws1.emitMessage(makeEvent(1));
    ws1.emitMessage(makeEvent(2));

    ws1.close(); // transport drop
    expect(MockWebSocket.instances).toHaveLength(1); // reconnect is scheduled, not immediate
    vi.advanceTimersByTime(500); // first backoff
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(MockWebSocket.instances[1].url).toContain('last_seq=2'); // resumes where it left off
  });

  it('applies exponential backoff between reconnects', () => {
    const client = new RealtimeClient('demo-1', 'tok', 'ws://test');
    client.connect();

    MockWebSocket.instances[0].close();
    vi.advanceTimersByTime(499);
    expect(MockWebSocket.instances).toHaveLength(1); // not until 500ms
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(2);

    MockWebSocket.instances[1].close();
    vi.advanceTimersByTime(999);
    expect(MockWebSocket.instances).toHaveLength(2); // second backoff is 1000ms
    vi.advanceTimersByTime(1);
    expect(MockWebSocket.instances).toHaveLength(3);
  });

  it('stops reconnecting after close()', () => {
    const client = new RealtimeClient('demo-1', 'tok', 'ws://test');
    client.connect();
    client.close();
    vi.advanceTimersByTime(5000);
    // Only the original socket was ever created.
    expect(MockWebSocket.instances).toHaveLength(1);
  });
});
