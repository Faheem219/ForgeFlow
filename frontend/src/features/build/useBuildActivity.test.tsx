import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { EVENT_BUFFER, appendEvent, useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { RealtimeEvent } from '../../lib/wsClient';
import { BUILD_STEPS, STEP_LABELS, useBuildActivity } from './useBuildActivity';

const PROJECT = 'p1';
let seq = 0;

function write(path: string, projectId = PROJECT): RealtimeEvent {
  seq += 1;
  return {
    event: 'fs.write',
    project_id: projectId,
    stage: 'build',
    payload: { path },
    seq,
    ts: '',
  };
}

function progress(step: string): RealtimeEvent {
  seq += 1;
  return {
    event: 'progress',
    project_id: PROJECT,
    stage: 'build',
    payload: { stage: 'build', step },
    seq,
    ts: '',
  };
}

function phase(
  id: string,
  title: string,
  kind: string,
  status: string,
  index: number,
  total: number,
): RealtimeEvent {
  seq += 1;
  return {
    event: 'build.phase',
    project_id: PROJECT,
    stage: 'build',
    payload: { stage: 'build', phase_id: id, title, kind, status, index, total },
    seq,
    ts: '',
  };
}

/** Push events onto the channel the way the websocket client does. */
function emit(...events: RealtimeEvent[]): void {
  act(() => {
    useRealtimeStore.setState((s) => ({ events: [...s.events, ...events] }));
  });
}

beforeEach(() => {
  seq = 0;
  useRealtimeStore.setState({ channel: PROJECT, events: [] });
});

afterEach(() => useRealtimeStore.setState({ channel: null, events: [] }));

describe('useBuildActivity', () => {
  it('collects generated files in arrival order as writes stream in', () => {
    const { result } = renderHook(() => useBuildActivity(PROJECT));

    emit(write('backend/src/todo/model.ts'));
    expect(result.current.files).toEqual(['backend/src/todo/model.ts']);

    emit(write('backend/src/todo/routes.ts'), write('frontend/src/TodoPage.tsx'));
    expect(result.current.files).toEqual([
      'backend/src/todo/model.ts',
      'backend/src/todo/routes.ts',
      'frontend/src/TodoPage.tsx',
    ]);
    expect(result.current.lastFile).toBe('frontend/src/TodoPage.tsx');
  });

  it('tracks the current build step', () => {
    const { result } = renderHook(() => useBuildActivity(PROJECT));

    emit(progress('plan'));
    expect(result.current.step).toBe('plan');

    emit(progress('implement'));
    expect(result.current.step).toBe('implement');
  });

  it('does not replay the pre-mount backlog as if it were landing now', () => {
    // The workspace connects before the Build panel mounts, so the store already holds an earlier
    // run's writes — showing them would fake progress that is not happening.
    useRealtimeStore.setState({
      channel: PROJECT,
      events: [write('stale/from-a-previous-run.ts')],
    });

    const { result } = renderHook(() => useBuildActivity(PROJECT));
    expect(result.current.files).toEqual([]);

    emit(write('fresh.ts'));
    expect(result.current.files).toEqual(['fresh.ts']);
  });

  it('ignores writes belonging to another project', () => {
    const { result } = renderHook(() => useBuildActivity(PROJECT));

    emit(write('other/file.ts', 'p2'), write('mine.ts'));
    expect(result.current.files).toEqual(['mine.ts']);
  });

  it('does not list the same path twice when a file is rewritten', () => {
    const { result } = renderHook(() => useBuildActivity(PROJECT));

    emit(write('a.ts'));
    emit(write('a.ts'));
    expect(result.current.files).toEqual(['a.ts']);
  });

  it('keeps tracking the build after terminal output saturates the event ring', () => {
    // A build runs `pnpm install` and leaves `pnpm dev` pumping, each chunk its own event. The
    // ring is bounded, so it saturates within seconds — and an index-based cursor stops advancing
    // the moment it does, which is what silently killed live build telemetry part-way through a
    // build (and only ever in the build stage, the only one loud enough to fill the ring).
    const { result } = renderHook(() => useBuildActivity(PROJECT));

    emit(write('early.ts'));
    expect(result.current.files).toEqual(['early.ts']);

    act(() => {
      useRealtimeStore.setState((s) => {
        let events = s.events;
        for (let i = 0; i < EVENT_BUFFER * 2; i += 1) {
          seq += 1;
          events = appendEvent(events, {
            event: 'terminal.output',
            project_id: PROJECT,
            stage: 'build',
            payload: {
              chunk: `line ${i}
`,
            },
            seq,
            ts: '',
          });
        }
        return { events };
      });
    });

    emit(progress('implement'), write('late.ts'));

    expect(result.current.step).toBe('implement');
    expect(result.current.files).toContain('late.ts');
  });

  it('starts a fresh list when a new run begins', () => {
    const { result, rerender } = renderHook(({ key }) => useBuildActivity(PROJECT, key), {
      initialProps: { key: 0 },
    });

    emit(write('run-one.ts'));
    expect(result.current.files).toEqual(['run-one.ts']);

    rerender({ key: 1 }); // a new build was submitted
    expect(result.current.files).toEqual([]);

    emit(write('run-two.ts'));
    expect(result.current.files).toEqual(['run-two.ts']);
  });

  // ---------------------------------------------------------------- phase rail (phase-56)

  it('tracks phases, index and total from build.phase events', () => {
    const { result } = renderHook(() => useBuildActivity(PROJECT));

    emit(phase('be-core', 'Backend API', 'backend', 'running', 1, 3));
    expect(result.current.phaseIndex).toBe(1);
    expect(result.current.phaseTotal).toBe(3);
    expect(result.current.phases).toEqual([
      { id: 'be-core', title: 'Backend API', kind: 'backend', status: 'running', index: 1 },
    ]);

    // The terminal announcement replaces the running one rather than adding a second row.
    emit(
      phase('be-core', 'Backend API', 'backend', 'done', 1, 3),
      phase('fe-core', 'Frontend', 'frontend', 'running', 2, 3),
    );
    expect(result.current.phases.map((p) => [p.id, p.status])).toEqual([
      ['be-core', 'done'],
      ['fe-core', 'running'],
    ]);
    expect(result.current.phaseIndex).toBe(2);
  });

  it('keeps the rail in plan order however the events arrive', () => {
    const { result } = renderHook(() => useBuildActivity(PROJECT));

    emit(
      phase('wiring', 'Wiring', 'wiring', 'pending', 3, 3),
      phase('be-core', 'Backend API', 'backend', 'done', 1, 3),
    );
    expect(result.current.phases.map((p) => p.id)).toEqual(['be-core', 'wiring']);
  });

  it('surfaces the verify stream so the panel does not sit on the last phase label', () => {
    const { result } = renderHook(() => useBuildActivity(PROJECT));

    seq += 1;
    emit({
      event: 'build.verify',
      project_id: PROJECT,
      stage: 'build',
      payload: { stage: 'build', step: 'typecheck' },
      seq,
      ts: '',
    });

    expect(result.current.step).toBe('verify');
    expect(result.current.label).toBe(STEP_LABELS.verify);
    expect(result.current.target).toBe('typecheck');
  });

  it('clears the rail when a new run begins', () => {
    const { result, rerender } = renderHook(({ key }) => useBuildActivity(PROJECT, key), {
      initialProps: { key: 0 },
    });

    emit(phase('be-core', 'Backend API', 'backend', 'done', 1, 3));
    expect(result.current.phases).toHaveLength(1);

    rerender({ key: 1 });
    expect(result.current.phases).toEqual([]);
    expect(result.current.phaseIndex).toBe(0);
  });

  // The direct regression guard for the 3-vs-7 mismatch: a step with no label collapses the
  // panel's sentence to a generic "Generating…", which is what it did for `survey` / `report`.
  it('has a non-empty label for every step in BUILD_STEPS', () => {
    for (const step of BUILD_STEPS) {
      expect(STEP_LABELS[step], `no label for "${step}"`).toBeTruthy();
    }
  });

  it('keeps `failed` labelled but out of the dot list (it is terminal, not a step)', () => {
    expect(STEP_LABELS.failed).toBeTruthy();
    expect([...BUILD_STEPS]).not.toContain('failed');
  });
});
