import { create } from 'zustand';

import { RealtimeClient, type ConnectionStatus, type RealtimeEvent } from '../wsClient';
import { useAuthStore } from './authStore';

/** How many events to retain per channel. Enough to replay a full agent turn's activity. */
export const EVENT_BUFFER = 400;

interface RealtimeState {
  status: ConnectionStatus;
  channel: string | null;
  events: RealtimeEvent[];
  connect: (projectId: string) => void;
  disconnect: () => void;
  dropSocket: () => void;
  clearEvents: () => void;
}

let client: RealtimeClient | null = null;
/** Unsubscribes for the CURRENT client. Held module-side so a swap can release them explicitly. */
let unsubscribes: (() => void)[] = [];

function teardown(): void {
  unsubscribes.forEach((off) => off());
  unsubscribes = [];
  client?.close();
  client = null;
}

export const useRealtimeStore = create<RealtimeState>((set) => ({
  status: 'closed',
  channel: null,
  events: [],
  connect: (projectId) => {
    const token = useAuthStore.getState().token;
    if (!token) return;
    // Release the previous client's subscriptions BEFORE creating the next one. Without this the
    // old closures stay registered and keep pushing into the store after a project switch.
    teardown();

    const next = new RealtimeClient(projectId, token);
    unsubscribes = [
      next.onStatus((status) => set({ status })),
      next.onEvent((event) => set((s) => ({ events: [...s.events, event].slice(-EVENT_BUFFER) }))),
    ];
    client = next;
    set({ channel: projectId, events: [] });
    next.connect();
  },
  disconnect: () => {
    teardown();
    set({ status: 'closed', channel: null });
  },
  dropSocket: () => client?.dropConnection(),
  clearEvents: () => set({ events: [] }),
}));
