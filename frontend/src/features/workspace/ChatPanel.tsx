import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import { Button, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import type { MessageDto, MessageRole, Stage } from '../../lib/types';
import { deriveTaskStream } from '../../lib/useTaskStream';
import { listMessages, submitIntent } from './api';
import { STAGE_LABELS } from './stageMeta';

const ROLE_STYLES: Record<MessageRole, string> = {
  user: 'bg-brand-600/20 text-brand-100 self-end',
  assistant: 'bg-slate-800 text-slate-100 self-start',
  system: 'bg-slate-800/50 text-slate-400 self-start italic',
  tool: 'bg-slate-800/50 text-emerald-300 self-start font-mono text-xs',
};

interface ChatPanelProps {
  projectId: string;
  activeStage: Stage;
}

export function ChatPanel({ projectId, activeStage }: ChatPanelProps): JSX.Element {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState('');
  const [activityOpen, setActivityOpen] = useState(false);

  const messagesQuery = useQuery({
    queryKey: ['messages', projectId, activeStage],
    queryFn: () => listMessages(projectId, activeStage),
  });

  const events = useRealtimeStore((s) => s.events);
  const channel = useRealtimeStore((s) => s.channel);

  // Live activity for this project's channel; agent tokens are streamed as they arrive.
  const activity = useMemo(
    () => events.filter((e) => e.project_id === channel && e.event !== 'ping'),
    [events, channel],
  );
  // Streamed text is folded PER TURN rather than by concatenating every `agent.token` still in the
  // ring buffer. Without the turn bracket (`agent.stream.start` resets, `run.finished` closes), a
  // finished turn's text lingered here across stages and across projects — there was no event that
  // would ever clear it. See lib/useTaskStream.ts.
  const streamed = useMemo(
    () => deriveTaskStream(events, projectId, 0).streamed,
    [events, projectId],
  );

  const send = useMutation({
    mutationFn: (message: string) =>
      submitIntent(projectId, { stage: activeStage, action: 'refine', message }),
    onSuccess: () => {
      setDraft('');
      // An intent can produce *anything* for the active stage — a new design version, a test run,
      // a build artifact. Refreshing only messages+stages left the stage panel showing stale
      // output (a design refine that had already landed was invisible until a reload), so every
      // query scoped to this project is invalidated. Blunt, but it cannot go stale again when a
      // new panel is added.
      void queryClient.invalidateQueries({
        predicate: (query) => query.queryKey.includes(projectId),
      });
    },
    onError: (err) => {
      const message = err instanceof ApiError ? err.message : 'Failed to send';
      toast({ title: 'Could not send', description: message, variant: 'error' });
    },
  });

  function onSubmit(event: React.FormEvent): void {
    event.preventDefault();
    const text = draft.trim();
    if (text) {
      send.mutate(text);
    }
  }

  const messages: MessageDto[] = messagesQuery.data ?? [];

  // Follow the tail while the agent streams, so the newest tokens stay in view.
  const tailRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    // Optional-called: jsdom (and older browsers) do not implement scrollIntoView.
    tailRef.current?.scrollIntoView?.({ block: 'end' });
  }, [messages.length, streamed]);

  return (
    // `min-h-0` all the way down is what lets the transcript scroll instead of growing the column
    // and shoving the composer out of view — the failure this layout previously had.
    <div className="flex h-full min-h-0 flex-col">
      <div className="shrink-0 border-b border-slate-800 px-4 py-2 text-xs uppercase tracking-wide text-slate-500">
        {STAGE_LABELS[activeStage]} conversation
      </div>

      <div
        data-testid="chat-messages"
        className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto p-4"
      >
        {messages.length === 0 && !streamed ? (
          <p className="text-sm text-slate-500">No messages yet. Say something to get started.</p>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={`max-w-[85%] whitespace-pre-wrap break-words rounded-lg px-3 py-2 text-sm ${ROLE_STYLES[m.role]}`}
            >
              {m.content}
            </div>
          ))
        )}

        {/* The agent's in-flight answer lives in the transcript (not a cramped strip below it), so
            a long build plan simply scrolls like any other message. */}
        {streamed ? (
          <div className="max-w-[85%] self-start rounded-lg bg-slate-800/70 px-3 py-2">
            <p className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-slate-500">
              <Spinner size="sm" />
              streaming
            </p>
            <p
              data-testid="chat-stream"
              className="whitespace-pre-wrap break-words text-sm text-slate-200"
            >
              {streamed}
            </p>
          </div>
        ) : null}
        <div ref={tailRef} />
      </div>

      {activity.length > 0 ? (
        <div data-testid="chat-activity" className="shrink-0 border-t border-slate-800">
          <button
            type="button"
            aria-expanded={activityOpen}
            data-testid="chat-activity-toggle"
            onClick={() => setActivityOpen((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-1.5 text-left text-xs uppercase tracking-wide text-slate-500 hover:bg-slate-800/40"
          >
            Activity
            <span className="rounded-full bg-slate-800 px-1.5 text-[10px] text-slate-400">
              {activity.length}
            </span>
            <span className="ml-auto" aria-hidden>
              {activityOpen ? '▾' : '▸'}
            </span>
          </button>
          {activityOpen ? (
            <ul className="max-h-24 space-y-0.5 overflow-auto px-4 pb-2">
              {activity.slice(-8).map((e) => (
                <li key={`${e.project_id}-${e.seq}`} className="text-xs text-slate-500">
                  <span className="text-slate-400">{e.event}</span>
                  {e.stage ? ` · ${e.stage}` : ''}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <form
        onSubmit={onSubmit}
        className="flex shrink-0 items-end gap-2 border-t border-slate-800 p-3"
      >
        <textarea
          aria-label="Message"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={`Refine ${STAGE_LABELS[activeStage]}…`}
          rows={2}
          className="flex-1 resize-none rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-brand-500"
        />
        <Button type="submit" loading={send.isPending} disabled={!draft.trim()}>
          Send
        </Button>
      </form>
    </div>
  );
}
