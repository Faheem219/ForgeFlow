// Reconnecting realtime client. Tracks last_seq for resume, dedupes by seq, ignores pings.

import { apiBaseUrl } from './apiClient';

export interface RealtimeEvent {
  event: string;
  project_id: string;
  stage: string | null;
  payload: Record<string, unknown>;
  seq: number;
  ts: string;
}

export type ConnectionStatus = 'connecting' | 'open' | 'closed';

type EventListener = (event: RealtimeEvent) => void;
type StatusListener = (status: ConnectionStatus) => void;

function defaultWsBase(): string {
  return apiBaseUrl.replace(/^http/i, 'ws');
}

export class RealtimeClient {
  private ws: WebSocket | null = null;
  private lastSeq = 0;
  private reconnectAttempts = 0;
  private shouldRun = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private status: ConnectionStatus = 'closed';
  private readonly eventListeners = new Set<EventListener>();
  private readonly statusListeners = new Set<StatusListener>();

  constructor(
    private readonly projectId: string,
    private readonly token: string,
    private readonly baseUrl: string = defaultWsBase(),
  ) {}

  connect(): void {
    this.shouldRun = true;
    this.open();
  }

  private open(): void {
    this.setStatus('connecting');
    const url =
      `${this.baseUrl}/ws/projects/${encodeURIComponent(this.projectId)}` +
      `?token=${encodeURIComponent(this.token)}&last_seq=${this.lastSeq}`;
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.setStatus('open');
    };
    ws.onmessage = (evt) => {
      let data: RealtimeEvent;
      try {
        data = JSON.parse(evt.data as string) as RealtimeEvent;
      } catch {
        return;
      }
      if (data.event === 'ping') return; // heartbeat / connected signal
      if (data.seq <= this.lastSeq) return; // dedup replay/live overlap
      this.lastSeq = data.seq;
      this.eventListeners.forEach((l) => l(data));
    };
    ws.onclose = () => {
      this.setStatus('closed');
      if (this.shouldRun) this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  private scheduleReconnect(): void {
    const delay = Math.min(30000, 500 * 2 ** this.reconnectAttempts);
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => this.open(), delay);
  }

  /** Force a transport reconnect (keeps lastSeq) to demonstrate resume. */
  dropConnection(): void {
    this.ws?.close();
  }

  onEvent(listener: EventListener): () => void {
    this.eventListeners.add(listener);
    return () => {
      this.eventListeners.delete(listener);
    };
  }

  onStatus(listener: StatusListener): () => void {
    this.statusListeners.add(listener);
    listener(this.status);
    return () => {
      this.statusListeners.delete(listener);
    };
  }

  close(): void {
    this.shouldRun = false;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.setStatus('closed');
  }

  private setStatus(status: ConnectionStatus): void {
    this.status = status;
    this.statusListeners.forEach((l) => l(status));
  }
}
