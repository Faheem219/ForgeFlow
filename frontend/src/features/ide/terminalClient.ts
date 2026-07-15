// Terminal WS transport. Unlike the typed realtime channel this carries RAW BYTES so TTY
// fidelity (control chars, colours, Ctrl-C) survives: binary frames are data, text frames are
// JSON control messages (resize).

import { apiBaseUrl } from '../../lib/apiClient';

export type TerminalStatus = 'connecting' | 'open' | 'closed';

type DataListener = (data: Uint8Array) => void;
type StatusListener = (status: TerminalStatus) => void;

function defaultWsBase(): string {
  return apiBaseUrl.replace(/^http/i, 'ws');
}

export class TerminalClient {
  private ws: WebSocket | null = null;
  private shouldRun = false;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private status: TerminalStatus = 'closed';
  private cols = 80;
  private rows = 24;
  private readonly dataListeners = new Set<DataListener>();
  private readonly statusListeners = new Set<StatusListener>();

  constructor(
    private readonly projectId: string,
    private readonly token: string,
    private readonly baseUrl: string = defaultWsBase(),
  ) {}

  connect(cols = this.cols, rows = this.rows): void {
    this.cols = cols;
    this.rows = rows;
    this.shouldRun = true;
    this.open();
  }

  private open(): void {
    this.setStatus('connecting');
    const url =
      `${this.baseUrl}/ws/projects/${encodeURIComponent(this.projectId)}/terminal` +
      `?token=${encodeURIComponent(this.token)}&cols=${this.cols}&rows=${this.rows}`;
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    ws.onopen = () => {
      this.reconnectAttempts = 0;
      this.setStatus('open');
    };
    ws.onmessage = (evt) => {
      const data = evt.data as ArrayBuffer | string;
      const bytes =
        typeof data === 'string' ? new TextEncoder().encode(data) : new Uint8Array(data);
      this.dataListeners.forEach((l) => l(bytes));
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

  /** Send keystrokes (Ctrl-C is just 0x03 — the sandbox TTY turns it into SIGINT). */
  send(data: string): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(new TextEncoder().encode(data));
  }

  resize(cols: number, rows: number): void {
    this.cols = cols;
    this.rows = rows;
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: 'resize', cols, rows }));
  }

  onData(listener: DataListener): () => void {
    this.dataListeners.add(listener);
    return () => {
      this.dataListeners.delete(listener);
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

  private setStatus(status: TerminalStatus): void {
    this.status = status;
    this.statusListeners.forEach((l) => l(status));
  }
}
