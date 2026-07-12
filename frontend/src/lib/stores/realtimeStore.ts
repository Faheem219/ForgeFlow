import { create } from 'zustand';

import { RealtimeClient, type ConnectionStatus, type RealtimeEvent } from '../wsClient';
import { useAuthStore } from './authStore';

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

export const useRealtimeStore = create<RealtimeState>((set) => ({
  status: 'closed',
  channel: null,
  events: [],
  connect: (projectId) => {
    const token = useAuthStore.getState().token;
    if (!token) return;
    client?.close();
    const next = new RealtimeClient(projectId, token);
    next.onStatus((status) => set({ status }));
    next.onEvent((event) => set((s) => ({ events: [...s.events, event].slice(-200) })));
    client = next;
    set({ channel: projectId, events: [] });
    next.connect();
  },
  disconnect: () => {
    client?.close();
    client = null;
    set({ status: 'closed', channel: null });
  },
  dropSocket: () => client?.dropConnection(),
  clearEvents: () => set({ events: [] }),
}));
