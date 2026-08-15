// Live-generation mode: turn `fs.write` events (phase-12) into a read-only stream in the editor.
//
// Two things the event contract forces on us:
//  1. `fs.write` carries only a path, so we re-read the file to get its content.
//  2. The backend echoes OUR OWN saves as `fs.write` too, so self-writes are suppressed —
//     otherwise every save would look like an agent write and raise a bogus conflict.
//
// There is no "generation finished" event yet (the codegen loop is phase-23), so the unlock is a
// quiet-period debounce: once writes to a path stop, the file becomes editable again.

import { useEffect, useRef } from 'react';

import { advanceCursor, highWaterMark } from '../../lib/eventCursor';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { readFile } from './api';
import { useIdeStore } from './ideStore';

export const UNLOCK_QUIET_MS = 1200;

const unlockTimers = new Map<string, ReturnType<typeof setTimeout>>();

function scheduleUnlock(path: string): void {
  const existing = unlockTimers.get(path);
  if (existing) clearTimeout(existing);
  unlockTimers.set(
    path,
    setTimeout(() => {
      unlockTimers.delete(path);
      useIdeStore.getState().endGenerating(path);
    }, UNLOCK_QUIET_MS),
  );
}

/** Test/teardown helper — drop pending unlock timers. */
export function clearUnlockTimers(): void {
  unlockTimers.forEach((t) => clearTimeout(t));
  unlockTimers.clear();
}

/**
 * Apply a server-side write to the IDE. Exported (not just used by the hook) so the behaviour is
 * testable without mounting React.
 */
export async function applyServerWrite(projectId: string, path: string): Promise<void> {
  const store = useIdeStore.getState();

  if (store.consumeSelfWrite(path)) return; // our own save coming back — not an agent write

  const open = store.files[path];
  // Never clobber unsaved user edits: ask instead.
  if (open && open.content !== open.saved) {
    store.setConflict(path);
    return;
  }

  let content: string;
  try {
    content = (await readFile(projectId, path)).content;
  } catch {
    return; // file may have been deleted again mid-flight; nothing to show
  }

  useIdeStore.getState().streamIn(path, content);
  scheduleUnlock(path);
}

/** Subscribe the IDE to this project's `fs.write` events. */
export function useLiveWrites(projectId: string): void {
  const events = useRealtimeStore((s) => s.events);
  // Cursored by the hub's monotonic `seq`, never by an array index: the store's ring evicts its
  // oldest entries once full, so an index cursor freezes at the cap and every later write is
  // dropped — which is exactly what stopped the IDE mid-build. See lib/eventCursor.ts.
  const processed = useRef(0);
  const baselinedFor = useRef<string | null>(null);

  useEffect(() => {
    // The realtime store buffers events from before the IDE mounted (the workspace connects
    // first). Replaying that backlog would spray open a tab per past write, so only react to
    // writes that happen while we're watching. Re-baselines on project switch.
    if (baselinedFor.current !== projectId) {
      baselinedFor.current = projectId;
      processed.current = highWaterMark(events);
      return;
    }

    const { fresh, nextSeq } = advanceCursor(events, processed.current);
    processed.current = nextSeq;
    for (const event of fresh) {
      if (event.event !== 'fs.write' || event.project_id !== projectId) continue;
      const path = typeof event.payload.path === 'string' ? event.payload.path : '';
      if (path) void applyServerWrite(projectId, path);
    }
  }, [events, projectId]);

  useEffect(() => () => clearUnlockTimers(), []);
}
