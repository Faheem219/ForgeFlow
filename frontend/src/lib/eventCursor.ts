// Cursors into the realtime event log.
//
// The log is a *bounded ring* (see stores/realtimeStore): appending past its capacity drops the
// oldest entries, so `events.length` stops growing once it saturates. That makes an array index a
// broken cursor — `processed = events.length` pins at the cap, `events.slice(processed)` returns
// `[]` forever, and the consumer goes permanently deaf while events keep arriving. Only the build
// stage produces enough events to saturate the ring, which is why it was the only stage where live
// file streaming and build telemetry silently stopped part-way through.
//
// Every event carries the hub's per-project monotonic `seq`, which keeps counting after eviction.
// Cursor on that instead.

import type { RealtimeEvent } from './wsClient';

/** Events newer than `sinceSeq`, oldest first. */
export function eventsSince(events: RealtimeEvent[], sinceSeq: number): RealtimeEvent[] {
  return events.filter((event) => event.seq > sinceSeq);
}

/**
 * The highest `seq` in the log — the "everything up to here is already handled" mark.
 *
 * The log is append-ordered, but a project switch clears it and the next channel restarts at seq 1,
 * so the last element is not reliably the maximum across a reset. Take the real max; the log is at
 * most a few hundred entries.
 */
export function highWaterMark(events: RealtimeEvent[]): number {
  let mark = 0;
  for (const event of events) if (event.seq > mark) mark = event.seq;
  return mark;
}

/**
 * Advance a cursor over the log: the events to handle now, and the mark to store for next time.
 *
 * The next mark is the log's own head, not `max(sinceSeq, head)`. A head *below* the cursor can
 * only mean the log was reset (a project switch restarts `seq` at 1) — following it down
 * re-baselines onto the new channel instead of filtering out its whole life.
 */
export function advanceCursor(
  events: RealtimeEvent[],
  sinceSeq: number,
): { fresh: RealtimeEvent[]; nextSeq: number } {
  const head = highWaterMark(events);
  return { fresh: head < sinceSeq ? [] : eventsSince(events, sinceSeq), nextSeq: head };
}
