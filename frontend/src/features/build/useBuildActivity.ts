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

/**
 * Progress steps the codegen agent emits, in the order it runs them.
 *
 * These are dots in the panel, so the list must match what the backend actually emits: it was a
 * 3-element subset of a backend emitting six, which left `survey` / `report` / `failed` lighting no
 * dot and `STEP_LABELS[step]` `undefined` (collapsing the label to a generic "Generating…").
 * `failed` is deliberately in `STEP_LABELS` but NOT here — it is terminal, not a dot.
 */
export const BUILD_STEPS = [
  'instantiate_skeleton',
  'survey',
  'plan',
  'phase',
  'verify',
  'repair',
  'report',
] as const;

export const STEP_LABELS: Record<string, string> = {
  instantiate_skeleton: 'Scaffolding the skeleton',
  survey: 'Surveying existing code',
  plan: 'Planning the build',
  phase: 'Implementing a phase',
  implement: 'Writing feature code',
  verify: 'Verifying the build',
  repair: 'Repairing the build',
  report: 'Finishing up',
  failed: 'Build failed',
};

/** One row of the phase rail, as the live `build.phase` stream reports it. */
export interface LivePhase {
  id: string;
  title: string;
  kind: string;
  status: string;
  index: number;
}

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
  /** Phases seen this run, in plan order (phase-56). */
  phases: LivePhase[];
  /** 1-based index of the phase in flight, and the plan's size (0 before the first event). */
  phaseIndex: number;
  phaseTotal: number;
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
  const [phases, setPhases] = useState<LivePhase[]>([]);
  const [phaseIndex, setPhaseIndex] = useState(0);
  const [phaseTotal, setPhaseTotal] = useState(0);
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
      setPhases([]);
      setPhaseIndex(0);
      setPhaseTotal(0);
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
    const phaseUpdates: LivePhase[] = [];
    let latestStep = '';
    let latestLabel: string | null = null;
    let latestTarget: string | null = null;
    let latestPhaseIndex: number | null = null;
    let latestPhaseTotal: number | null = null;
    for (const event of fresh) {
      if (event.project_id !== projectId) continue;
      if (event.event === 'fs.write') {
        const path = event.payload.path;
        if (typeof path === 'string' && path) written.push(path);
      } else if (event.event === 'build.phase') {
        const id = String(event.payload.phase_id ?? '');
        if (!id) continue;
        const index = Number(event.payload.index ?? 0);
        phaseUpdates.push({
          id,
          title: String(event.payload.title ?? id),
          kind: String(event.payload.kind ?? ''),
          status: String(event.payload.status ?? 'pending'),
          index,
        });
        latestPhaseIndex = index;
        latestPhaseTotal = Number(event.payload.total ?? 0) || latestPhaseTotal;
      } else if (event.event === 'build.verify') {
        // The verify stream has no `progress` twin, so the panel would sit on the last phase's
        // label for the whole install/typecheck/boot wait without this.
        latestStep = 'verify';
        latestLabel = STEP_LABELS.verify;
        latestTarget = String(event.payload.step ?? '');
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
    if (phaseUpdates.length > 0) {
      // A phase is announced twice (running, then its terminal status); the newest wins, and the
      // rail stays in plan order rather than in arrival order.
      setPhases((prev) => {
        const byId = new Map(prev.map((p) => [p.id, p]));
        for (const update of phaseUpdates) byId.set(update.id, update);
        return [...byId.values()].sort((a, b) => a.index - b.index);
      });
    }
    if (latestPhaseIndex !== null) setPhaseIndex(latestPhaseIndex);
    if (latestPhaseTotal) setPhaseTotal(latestPhaseTotal);
  }, [events, projectId]);

  return useMemo(
    () => ({
      step,
      label,
      target,
      files,
      lastFile: files.at(-1) ?? null,
      phases,
      phaseIndex,
      phaseTotal,
    }),
    [step, label, target, files, phases, phaseIndex, phaseTotal],
  );
}
