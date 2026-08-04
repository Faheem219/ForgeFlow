import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { RealtimeEvent } from '../../lib/wsClient';
import { useBuildActivity } from './useBuildActivity';

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
});
