// Live build telemetry, read off the project's realtime channel (phase-23 emits both signals).
//
// The Build stage looks idle for a long time before anything appears on screen: step 0 instantiates
// the skeleton and step 1 *plans* (a whole model round-trip) without writing a single feature file,
// so the IDE has nothing to show until the implement step starts — and then a dozen files land at
// once. This hook surfaces what is actually happening in between: the current step, and each file
// as its `fs.write` arrives.

import { useEffect, useMemo, useRef, useState } from 'react';

import { advanceCursor, highWaterMark } from '../../lib/eventCursor';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';

/** Progress steps the codegen agent emits, in the order it runs them. */
export const BUILD_STEPS = ['instantiate_skeleton', 'plan', 'implement'] as const;

export const STEP_LABELS: Record<string, string> = {
  instantiate_skeleton: 'Scaffolding the skeleton',
  plan: 'Planning the build',
  implement: 'Writing feature code',
};

export interface BuildActivity {
  /** Latest `progress` step for the build stage ('' before the first one arrives). */
  step: string;
  /** Human sentence for the action in flight, e.g. "Installing dependencies". */
  label: string;
  /** The file or command that action concerns, when meaningful. */
  target: string;
  /** Files written by the agent this session, in arrival order (deduped, newest last). */
  files: string[];
  /** The most recent file to land, or null. */
  lastFile: string | null;
}

/**
 * Track build progress + streamed file writes for `projectId`.
 *
 * Events already in the store when this mounts are skipped: the workspace connects before the Build
 * panel renders, so replaying the backlog would show files from an earlier build as if they were
 * landing now. `resetKey` re-baselines (a new build clears the previous run's list).
 */
export function useBuildActivity(projectId: string, resetKey: unknown = null): BuildActivity {
  const events = useRealtimeStore((s) => s.events);
  const [files, setFiles] = useState<string[]>([]);
  const [step, setStep] = useState('');
  const [label, setLabel] = useState('');
  const [target, setTarget] = useState('');
  // The cursor is the hub's monotonic `seq`, not an index into `events`. The store's ring is
  // bounded, so once a build's traffic fills it the array stops growing and an index cursor pins
  // at the cap — `slice(cursor)` then yields nothing forever and this hook goes deaf mid-build.
  // See lib/eventCursor.ts.
  const cursor = useRef<number | null>(null);
  const baseline = useRef<string | null>(null);

  // Re-baseline on project switch or when a new run starts. The cursor is moved to the live event
  // mark *here* rather than on the next effect pass — deferring it would swallow any write that
  // arrives in between, which is exactly the window a fresh build starts in.
  useEffect(() => {
    const token = `${projectId}::${String(resetKey)}`;
    if (baseline.current !== token) {
      baseline.current = token;
      cursor.current = highWaterMark(useRealtimeStore.getState().events);
      setFiles([]);
      setStep('');
      setLabel('');
      setTarget('');
    }
  }, [projectId, resetKey]);

  useEffect(() => {
    if (cursor.current === null) {
      cursor.current = highWaterMark(events); // ignore the pre-mount backlog
      return;
    }
    const { fresh, nextSeq } = advanceCursor(events, cursor.current);
    // Park the cursor before the early return: a batch can be empty because the log RESET to a
    // lower seq, and leaving the mark above the new channel's head would strand the hook there.
    cursor.current = nextSeq;
    if (fresh.length === 0) return;

    const written: string[] = [];
    let latestStep = '';
    let latestLabel: string | null = null;
    let latestTarget: string | null = null;
    for (const event of fresh) {
      if (event.project_id !== projectId) continue;
      if (event.event === 'fs.write') {
        const path = event.payload.path;
        if (typeof path === 'string' && path) written.push(path);
      } else if (event.event === 'progress' && event.payload.stage === 'build') {
        latestStep = String(event.payload.step ?? '') || latestStep;
        // Per-tool events carry a label; coarse phase events carry one too. Either way the newest
        // wins, so the UI names what is happening *right now*.
        if (event.payload.label !== undefined) latestLabel = String(event.payload.label ?? '');
        latestTarget = event.payload.target === undefined ? '' : String(event.payload.target ?? '');
      }
    }
    if (latestStep) setStep(latestStep);
    if (latestLabel !== null) setLabel(latestLabel);
    if (latestTarget !== null) setTarget(latestTarget);
    if (written.length > 0) {
      setFiles((prev) => [...prev, ...written.filter((p) => !prev.includes(p))]);
    }
  }, [events, projectId]);

  return useMemo(
    () => ({ step, label, target, files, lastFile: files.at(-1) ?? null }),
    [step, label, target, files],
  );
}
