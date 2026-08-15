import { create } from 'zustand';

import { RealtimeClient, type ConnectionStatus, type RealtimeEvent } from '../wsClient';
import { useAuthStore } from './authStore';

/** How many events to retain per channel. Enough to replay a full agent turn's activity. */
export const EVENT_BUFFER = 800;

/**
 * Events emitted per *output chunk* rather than per meaningful step.
 *
 * A build runs `pnpm install` and then leaves `pnpm dev` pumping forever, and each chunk of that
 * output is one event on the same channel as the agent's own. Unpartitioned, a single ring lets
 * that traffic evict every `agent.token`, `fs.write` and `progress` within seconds — which is what
 * left the Build stage with a blank "streaming" bubble, an IDE that never opened the files the
 * agent was writing, and a progress label frozen on whatever step was current when the flood
 * started.
 */
const HIGH_VOLUME_EVENTS = new Set(['terminal.output']);

/**
 * The share of {@link EVENT_BUFFER} high-volume events may occupy.
 *
 * Deliberately equal to the buffer's previous *total* size, so terminal scrollback (Preview's log
 * tabs) retains exactly as much as it did before — the extra capacity is what is now reserved for
 * everything else, rather than anything being taken away.
 */
const HIGH_VOLUME_BUFFER = 400;

function isHighVolume(event: RealtimeEvent): boolean {
  return HIGH_VOLUME_EVENTS.has(event.event);
}

/**
 * Append to the ring, evicting oldest-first but never letting high-volume traffic starve the rest.
 *
 * Walks newest → oldest keeping at most `EVENT_BUFFER` events overall and at most
 * `HIGH_VOLUME_BUFFER` of them high-volume, so at least `EVENT_BUFFER - HIGH_VOLUME_BUFFER` slots
 * always remain available to agent/build events no matter how loud the terminal is. Arrival order
 * is preserved. Exported for the regression test that pins this guarantee.
 */
export function appendEvent(events: RealtimeEvent[], event: RealtimeEvent): RealtimeEvent[] {
  const next = [...events, event];
  if (next.length <= EVENT_BUFFER && !isHighVolume(event)) return next;

  const kept: RealtimeEvent[] = [];
  let highVolume = 0;
  for (let i = next.length - 1; i >= 0 && kept.length < EVENT_BUFFER; i -= 1) {
    const candidate = next[i];
    if (isHighVolume(candidate)) {
      if (highVolume >= HIGH_VOLUME_BUFFER) continue;
      highVolume += 1;
    }
    kept.push(candidate);
  }
  kept.reverse();
  return kept;
}

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
      next.onEvent((event) => set((s) => ({ events: appendEvent(s.events, event) }))),
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
