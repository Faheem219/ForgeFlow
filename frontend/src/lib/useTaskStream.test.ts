import { describe, expect, it } from 'vitest';

import { deriveTaskStream } from './useTaskStream';
import type { RealtimeEvent } from './wsClient';

let seq = 0;

function event(
  name: string,
  payload: Record<string, unknown> = {},
  projectId = 'p1',
): RealtimeEvent {
  seq += 1;
  return {
    event: name,
    project_id: projectId,
    stage: null,
    payload,
    seq,
    ts: new Date().toISOString(),
  };
}

describe('deriveTaskStream', () => {
  it('accumulates tokens between stream start and end', () => {
    const events = [
      event('agent.stream.start'),
      event('agent.token', { text: 'Hello ' }),
      event('agent.token', { text: 'world' }),
      event('agent.stream.end', { completed: true }),
    ];

    const state = deriveTaskStream(events, 'p1', 0);
    expect(state.streamed).toBe('Hello world');
    expect(state.streaming).toBe(false);
  });

  it('resets streamed text on a new turn rather than concatenating turns', () => {
    const events = [
      event('agent.stream.start'),
      event('agent.token', { text: 'first turn' }),
      event('agent.stream.end'),
      event('agent.stream.start'),
      event('agent.token', { text: 'second turn' }),
    ];

    const state = deriveTaskStream(events, 'p1', 0);
    expect(state.streamed).toBe('second turn');
    expect(state.streaming).toBe(true);
  });

  it('still shows tokens whose stream.start was never seen', () => {
    // A client joining mid-generation resumes against a bounded ring buffer, so the opening event
    // may have been evicted. Partial output beats a blank panel while the model is visibly working.
    const state = deriveTaskStream([event('agent.token', { text: 'orphan' })], 'p1', 0);
    expect(state.streamed).toBe('orphan');
    expect(state.streaming).toBe(true);
  });

  it('marks the task finished on run.finished — the terminal signal', () => {
    const events = [
      event('agent.stream.start'),
      event('agent.token', { text: 'building' }),
      event('run.finished', { ok: true, run_id: 'r1' }),
    ];

    const state = deriveTaskStream(events, 'p1', 0);
    expect(state.finished).toBe(true);
    expect(state.streaming).toBe(false);
    expect(state.error).toBeNull();
  });

  it('clears the streamed turn text on run.finished, not just the streaming flag', () => {
    // A client that reconnects and replays a completed run's buffered history (or one that just
    // stays mounted past the terminal event) must not keep showing the in-flight bubble forever —
    // it duplicates content the server already persisted into `messages`.
    const events = [
      event('agent.stream.start'),
      event('agent.token', { text: 'building the app' }),
      event('run.finished', { ok: true, run_id: 'r1' }),
    ];

    const state = deriveTaskStream(events, 'p1', 0);
    expect(state.streamed).toBe('');
    expect(state.finished).toBe(true);
  });

  it('surfaces the failure message when a run ends badly', () => {
    const state = deriveTaskStream(
      [event('run.finished', { ok: false, error: 'budget exhausted' })],
      'p1',
      0,
    );
    expect(state.finished).toBe(true);
    expect(state.error).toBe('budget exhausted');
  });

  it('ignores a terminal event from a previous task via the seq baseline', () => {
    const stale = event('run.finished', { ok: true });
    const fresh = event('agent.stream.start');

    // Baseline = the stale event's seq, i.e. the task started after it.
    const state = deriveTaskStream([stale, fresh], 'p1', stale.seq);
    expect(state.finished).toBe(false);
    expect(state.streaming).toBe(true);
  });

  it('ignores events belonging to another project', () => {
    const state = deriveTaskStream([event('run.finished', { ok: true }, 'other-project')], 'p1', 0);
    expect(state.finished).toBe(false);
  });

  it('tracks the latest progress step for the watched stage only', () => {
    const events = [
      event('progress', { stage: 'build', step: 'plan' }),
      event('progress', { stage: 'test', step: 'author' }),
      event('progress', { stage: 'build', step: 'implement' }),
    ];

    expect(deriveTaskStream(events, 'p1', 0, 'build').step).toBe('implement');
    expect(deriveTaskStream(events, 'p1', 0, 'test').step).toBe('author');
  });

  it('closes the stream bracket even when the turn never completed', () => {
    // `agent.stream.end` is emitted from a `finally` server-side, so a failed turn still ends.
    const events = [
      event('agent.stream.start'),
      event('agent.token', { text: 'partial' }),
      event('agent.stream.end', { completed: false }),
    ];

    expect(deriveTaskStream(events, 'p1', 0).streaming).toBe(false);
  });
});
