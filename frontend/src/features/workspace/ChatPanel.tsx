import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, SendHorizontal } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { Button, Kbd, Spinner } from '../../components/ui';
import { cn } from '../../lib/cn';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import type { MessageDto, MessageRole, Stage } from '../../lib/types';
import { deriveTaskStream } from '../../lib/useTaskStream';
import { listMessages, submitIntent } from './api';
import { STAGE_LABELS } from './stageMeta';

const ROLE_STYLES: Record<MessageRole, string> = {
  user: 'bg-brand/15 text-fg self-end rounded-2xl rounded-br-md',
  assistant: 'bg-surface-raised text-fg self-start rounded-2xl rounded-bl-md',
  system: 'bg-surface-raised text-fg-muted self-start italic rounded-2xl',
  tool: 'bg-surface-raised text-success self-start font-mono text-xs rounded-2xl rounded-bl-md',
};

interface ChatPanelProps {
  projectId: string;
  activeStage: Stage;
}

export function ChatPanel({ projectId, activeStage }: ChatPanelProps): JSX.Element {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState('');
  const [activityOpen, setActivityOpen] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);

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
  // A turn that goes straight to tool use still emits a newline or two of text before it. Keying
  // the bubble off `streamed` alone rendered a "streaming" header over an empty paragraph for the
  // whole of a tool-heavy stage (build), which reads as a stream that broke rather than one that
  // has nothing to say yet.
  const hasStream = streamed.trim().length > 0;

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

  function submitDraft(): void {
    const text = draft.trim();
    if (text && !send.isPending) {
      send.mutate(text);
    }
  }

  function onSubmit(event: React.FormEvent): void {
    event.preventDefault();
    submitDraft();
  }

  // Enter sends; Shift+Enter keeps its newline — the convention every chat surface has taught.
  function onComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submitDraft();
    }
  }

  // Grow with the draft (up to a cap) instead of scrolling two visible lines.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

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
      <div className="shrink-0 border-b border-edge px-4 py-2 text-xs font-medium uppercase tracking-wide text-fg-subtle">
        {STAGE_LABELS[activeStage]} conversation
      </div>

      <div
        data-testid="chat-messages"
        className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto p-4"
      >
        {messages.length === 0 && !hasStream ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
            <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand/10 text-brand-text ring-1 ring-inset ring-brand/20">
              <SendHorizontal aria-hidden className="h-5 w-5" strokeWidth={1.5} />
            </span>
            <p className="max-w-[16rem] text-sm text-fg-subtle">
              No messages yet. Say something to get started.
            </p>
          </div>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={cn(
                'max-w-[85%] whitespace-pre-wrap break-words px-3.5 py-2 text-sm',
                ROLE_STYLES[m.role],
              )}
            >
              {m.content}
            </div>
          ))
        )}

        {/* The agent's in-flight answer lives in the transcript (not a cramped strip below it), so
            a long build plan simply scrolls like any other message. */}
        {hasStream ? (
          <div className="max-w-[85%] self-start rounded-2xl rounded-bl-md bg-surface-raised px-3.5 py-2">
            <p className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-fg-subtle">
              <Spinner size="sm" />
              streaming
            </p>
            <p
              data-testid="chat-stream"
              className="whitespace-pre-wrap break-words text-sm text-fg"
            >
              {streamed}
            </p>
          </div>
        ) : null}
        <div ref={tailRef} />
      </div>

      {activity.length > 0 ? (
        <div data-testid="chat-activity" className="shrink-0 border-t border-edge">
          <button
            type="button"
            aria-expanded={activityOpen}
            data-testid="chat-activity-toggle"
            onClick={() => setActivityOpen((v) => !v)}
            className="flex w-full items-center gap-2 px-4 py-1.5 text-left text-xs font-medium uppercase tracking-wide text-fg-subtle transition-colors hover:bg-surface-raised"
          >
            Activity
            <span className="rounded-full bg-surface-raised px-1.5 text-[10px] text-fg-muted">
              {activity.length}
            </span>
            <span className="ml-auto" aria-hidden>
              {activityOpen ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
            </span>
          </button>
          {activityOpen ? (
            <ul className="max-h-24 space-y-0.5 overflow-auto px-4 pb-2">
              {activity.slice(-8).map((e) => (
                <li key={`${e.project_id}-${e.seq}`} className="text-xs text-fg-subtle">
                  <span className="text-fg-muted">{e.event}</span>
                  {e.stage ? ` · ${e.stage}` : ''}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <form onSubmit={onSubmit} className="shrink-0 border-t border-edge p-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={composerRef}
            aria-label="Message"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onComposerKeyDown}
            placeholder={`Refine ${STAGE_LABELS[activeStage]}…`}
            rows={2}
            className="flex-1 resize-none rounded-xl border border-edge-strong bg-surface-sunken px-3 py-2 text-sm text-fg placeholder:text-fg-faint outline-none transition-colors focus:border-brand"
          />
          <Button
            type="submit"
            aria-label="Send"
            loading={send.isPending}
            disabled={!draft.trim()}
            className="h-9 w-9 shrink-0 px-0"
          >
            {send.isPending ? null : <SendHorizontal aria-hidden className="h-4 w-4" />}
          </Button>
        </div>
        <p className="mt-1.5 flex items-center gap-1 text-[11px] text-fg-faint">
          <Kbd>Enter</Kbd> to send · <Kbd>Shift</Kbd>+<Kbd>Enter</Kbd> for a new line
        </p>
      </form>
    </div>
  );
}
