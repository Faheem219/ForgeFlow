import { describe, expect, it } from 'vitest';

import { advanceCursor, eventsSince, highWaterMark } from './eventCursor';
import { EVENT_BUFFER, appendEvent } from './stores/realtimeStore';
import type { RealtimeEvent } from './wsClient';

function ev(event: string, seq: number, payload: Record<string, unknown> = {}): RealtimeEvent {
  return { event, project_id: 'p1', stage: 'build', payload, seq, ts: '' };
}

describe('eventCursor', () => {
  it('yields only events past the mark', () => {
    const log = [ev('progress', 1), ev('fs.write', 2), ev('fs.write', 3)];
    expect(eventsSince(log, 1).map((e) => e.seq)).toEqual([2, 3]);
    expect(highWaterMark(log)).toBe(3);
  });

  it('keeps advancing after the ring starts evicting', () => {
    // The whole point: `seq` outlives eviction, so a cursor on it does too. An index cursor pins
    // at the buffer cap here and never sees another event.
    let log = [ev('fs.write', 500, { path: 'a.ts' })];
    const { nextSeq } = advanceCursor(log, 0);
    expect(nextSeq).toBe(500);

    log = [ev('terminal.output', 900), ev('fs.write', 901, { path: 'b.ts' })]; // older entries gone
    const second = advanceCursor(log, nextSeq);
    expect(second.fresh.map((e) => e.seq)).toEqual([900, 901]);
    expect(second.nextSeq).toBe(901);
  });

  it('re-baselines when the log resets to a lower seq (project switch)', () => {
    const reset = advanceCursor([ev('progress', 1)], 400);
    expect(reset.fresh).toEqual([]);
    expect(reset.nextSeq).toBe(1); // follows the new channel down instead of going deaf
  });

  it('parks the cursor at the head when nothing is fresh', () => {
    const log = [ev('progress', 7)];
    expect(advanceCursor(log, 7)).toEqual({ fresh: [], nextSeq: 7 });
  });
});

describe('event ring partitioning', () => {
  it('never lets terminal output evict agent and build events', () => {
    // A build leaves `pnpm dev` pumping one event per output chunk. Before the ring was
    // partitioned that traffic flushed every agent.token / fs.write out within seconds.
    let log: RealtimeEvent[] = [];
    let seq = 0;
    const push = (event: string, payload: Record<string, unknown> = {}): void => {
      seq += 1;
      log = appendEvent(log, ev(event, seq, payload));
    };

    push('agent.stream.start', { model: 'm' });
    push('agent.token', { text: 'planning the build' });
    push('fs.write', { path: 'backend/src/app.ts' });

    for (let i = 0; i < EVENT_BUFFER * 3; i += 1) push('terminal.output', { chunk: `line ${i}\n` });

    expect(log.some((e) => e.event === 'agent.token')).toBe(true);
    expect(log.some((e) => e.event === 'fs.write')).toBe(true);
    expect(log.length).toBeLessThanOrEqual(EVENT_BUFFER);
  });

  it('keeps the ring ordered and bounded', () => {
    let log: RealtimeEvent[] = [];
    for (let seq = 1; seq <= EVENT_BUFFER * 2; seq += 1) {
      log = appendEvent(log, ev(seq % 2 === 0 ? 'terminal.output' : 'progress', seq));
    }
    expect(log.length).toBeLessThanOrEqual(EVENT_BUFFER);
    expect(log.map((e) => e.seq)).toEqual([...log.map((e) => e.seq)].sort((a, b) => a - b));
    expect(log.at(-1)?.seq).toBe(EVENT_BUFFER * 2);
  });
});
