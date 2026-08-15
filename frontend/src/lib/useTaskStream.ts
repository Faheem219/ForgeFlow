import { useMemo, useState } from 'react';

import { useRealtimeStore } from './stores/realtimeStore';
import type { RealtimeEvent } from './wsClient';

/**
 * Task-level busy state for long-running agent work.
 *
 * ## Why this exists
 *
 * `POST /projects/:id/intent` runs a whole stage handler inline — codegen can take minutes — while
 * the actual output streams over the WebSocket. Binding a spinner to that request's `isPending`
 * means the spinner's lifetime is the lifetime of an HTTP connection, not of the work. When the
 * connection is dropped by a proxy read-timeout, a sleeping laptop, or a stalled sandbox, the
 * promise never settles, `isPending` stays `true`, and the spinner runs forever — visibly, and
 * absurdly, *after* the build report has already rendered from the stream.
 *
 * So busy is:
 *
 *     pending AND NOT (a terminal event for this task has arrived)
 *
 * Whichever signal lands first wins. The HTTP response still resolves the mutation normally; this
 * just stops the UI from depending on it. `apiFetch`'s deadline is the third net beneath both.
 *
 * ## Baseline
 *
 * Events are read from a ring buffer that outlives any one task, so a `run.finished` from the
 * PREVIOUS build would instantly clear the next one. Every task therefore records the highest seq
 * seen at the moment it started, and only considers events after it.
 */

export interface TaskStreamState {
  /** True while work is genuinely in flight. Drive spinners from this, never from `isPending`. */
  busy: boolean;
  /** True between `agent.stream.start` and `agent.stream.end` — the model is emitting tokens. */
  streaming: boolean;
  /** Latest `progress` step for the watched stage, e.g. "implement". */
  step: string;
  /** Text streamed by the CURRENT model turn. Reset on each `agent.stream.start` and cleared on `run.finished`. */
  streamed: string;
  /** Set when the run reported failure; `null` on success or while still running. */
  error: string | null;
  /** True once a terminal `run.finished` for this task has been seen. */
  finished: boolean;
}

interface Derived {
  streaming: boolean;
  step: string;
  streamed: string;
  error: string | null;
  finished: boolean;
}

const EMPTY: Derived = {
  streaming: false,
  step: '',
  streamed: '',
  error: null,
  finished: false,
};

/**
 * Fold a slice of the event log into task state. Pure and exported so the lifecycle contract is
 * testable without mounting React or opening a socket.
 */
export function deriveTaskStream(
  events: RealtimeEvent[],
  projectId: string,
  sinceSeq: number,
  stage?: string,
): Derived {
  let out = { ...EMPTY };

  for (const event of events) {
    if (event.project_id !== projectId || event.seq <= sinceSeq) continue;

    switch (event.event) {
      case 'agent.stream.start':
        // A new turn supersedes the previous one's text — otherwise turns concatenate forever.
        // This is a RESET, not a gate: see the token case below.
        out = { ...out, streaming: true, streamed: '' };
        break;
      case 'agent.token':
        // Tokens are accepted whether or not a `stream.start` was seen. A client that joins
        // mid-generation resumes from `last_seq` against a bounded ring buffer, so the start event
        // may simply have been evicted — and showing partial output beats showing nothing while
        // the model is visibly working. A token is itself evidence that a stream is open.
        out = {
          ...out,
          streaming: true,
          streamed: out.streamed + String(event.payload.text ?? event.payload.token ?? ''),
        };
        break;
      case 'agent.stream.end':
        out = { ...out, streaming: false };
        break;
      case 'progress':
        if (!stage || event.payload.stage === stage) {
          const step = event.payload.step;
          if (typeof step === 'string' && step) out = { ...out, step };
        }
        break;
      case 'run.finished':
        if (!stage || event.payload.stage === stage) {
          out = {
            ...out,
            finished: true,
            streaming: false,
            // The turn's ephemeral text gives way to the durable message the server persisted —
            // without this, a client that reconnects and replays a completed run's buffered
            // history (or one that simply stays mounted past `run.finished`) keeps showing the
            // in-flight bubble forever, duplicating content that already landed in `messages`.
            streamed: '',
            error: event.payload.ok === false ? String(event.payload.error ?? 'Task failed') : null,
          };
        }
        break;
      default:
        break;
    }
  }

  return out;
}

export interface UseTaskStreamOptions {
  /** The driving mutation's `isPending`. */
  pending: boolean;
  /** Restrict `progress` / `run.finished` matching to one stage. */
  stage?: string;
}

export function useTaskStream(
  projectId: string,
  { pending, stage }: UseTaskStreamOptions,
): TaskStreamState {
  const events = useRealtimeStore((s) => s.events);

  // Snapshot the log's high-water mark at the instant the task starts.
  //
  // This is React's documented "adjust state when a prop changes" pattern — state rather than a
  // ref, deliberately. A ref would not be a valid `useMemo` dependency, so the fold would keep the
  // OLD baseline until the next event arrived; in that window a `run.finished` from the previous
  // task would still be visible and would clear the new task's spinner the moment it started.
  const [prevPending, setPrevPending] = useState(pending);
  const [baselineSeq, setBaselineSeq] = useState(0);
  if (pending !== prevPending) {
    setPrevPending(pending);
    if (pending) setBaselineSeq(events.length > 0 ? events[events.length - 1].seq : 0);
  }

  const derived = useMemo(
    () => deriveTaskStream(events, projectId, baselineSeq, stage),
    [events, projectId, stage, baselineSeq],
  );

  return {
    ...derived,
    busy: pending && !derived.finished,
  };
}
