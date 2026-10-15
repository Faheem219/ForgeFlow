import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUp, ChevronDown, MessageSquareText, Wrench } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { Button, Kbd, Spinner } from '../../components/ui';
import { cn } from '../../lib/cn';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import type { MessageDto, MessageRole, Stage } from '../../lib/types';
import { deriveTaskStream } from '../../lib/useTaskStream';
import { useNarrow } from '../../lib/useNarrow';
import { listMessages, submitIntent } from './api';
import { STAGE_LABELS } from './stageMeta';

/*
 * Only conversation gets a bubble. A tool call is a log line the agent left behind, so it reads as
 * one — small, monospaced, unboxed — and a system note sits centred between turns, like a divider.
 */
const ROLE_STYLES: Record<MessageRole, string> = {
  user: 'max-w-[88%] self-end rounded-2xl rounded-br-md bg-brand/[0.16] px-3.5 py-2 text-fg',
  assistant: 'max-w-[92%] self-start rounded-2xl rounded-bl-md bg-fg/[0.045] px-3.5 py-2 text-fg',
  system: 'max-w-full self-center px-2 text-center text-2xs italic text-fg-subtle',
  tool: 'max-w-full self-start font-mono text-2xs text-fg-subtle',
};

interface ChatPanelProps {
  projectId: string;
  activeStage: Stage;
}

export function ChatPanel({ projectId, activeStage }: ChatPanelProps): JSX.Element {
  const queryClient = useQueryClient();
  // Below ~300px the shortcut hint cannot fit on one line; Enter-to-send is convention enough.
  const [formRef, narrow] = useNarrow<HTMLFormElement>(300);
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
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-edge px-3.5">
        <MessageSquareText aria-hidden className="h-3.5 w-3.5 text-fg-subtle" strokeWidth={1.75} />
        <span className="text-xs font-medium text-fg-muted">
          {STAGE_LABELS[activeStage]} conversation
        </span>
      </div>

      <div
        data-testid="chat-messages"
        className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-auto px-3.5 py-4"
      >
        {messages.length === 0 && !hasStream ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-4 text-center">
            <MessageSquareText aria-hidden className="h-5 w-5 text-fg-faint" strokeWidth={1.5} />
            <p className="text-sm font-medium text-fg">No messages yet</p>
            <p className="max-w-[15rem] text-xs leading-relaxed text-fg-subtle">
              Say something to get started — whatever you ask for applies to{' '}
              {STAGE_LABELS[activeStage]}.
            </p>
          </div>
        ) : (
          messages.map((m) =>
            m.role === 'tool' ? (
              <div key={m.id} className={cn('flex items-start gap-1.5', ROLE_STYLES.tool)}>
                <Wrench
                  aria-hidden
                  className="mt-0.5 h-3 w-3 shrink-0 text-fg-faint"
                  strokeWidth={2}
                />
                <span className="min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]">
                  {m.content}
                </span>
              </div>
            ) : (
              <div
                key={m.id}
                className={cn(
                  'whitespace-pre-wrap break-words text-[13px] leading-relaxed',
                  ROLE_STYLES[m.role],
                )}
              >
                {m.content}
              </div>
            ),
          )
        )}

        {/* The agent's in-flight answer lives in the transcript (not a cramped strip below it), so
            a long build plan simply scrolls like any other message. */}
        {hasStream ? (
          <div className="max-w-[92%] self-start rounded-2xl rounded-bl-md bg-fg/[0.045] px-3.5 py-2">
            <p className="mb-1 flex items-center gap-1.5 text-2xs font-medium text-brand-text">
              <Spinner size="sm" />
              Streaming
            </p>
            <p
              data-testid="chat-stream"
              className="whitespace-pre-wrap break-words text-[13px] leading-relaxed text-fg"
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
            className="flex h-8 w-full items-center gap-2 px-3.5 text-left text-2xs font-medium text-fg-subtle transition-colors hover:bg-fg/[0.03] hover:text-fg-muted"
          >
            Activity
            <span className="rounded bg-fg/[0.07] px-1 tabular-nums text-fg-muted">
              {activity.length}
            </span>
            <ChevronDown
              aria-hidden
              className={cn(
                'ml-auto h-3.5 w-3.5 transition-transform duration-200',
                activityOpen ? 'rotate-180' : '',
              )}
            />
          </button>
          {activityOpen ? (
            <ul className="max-h-28 space-y-0.5 overflow-auto px-3.5 pb-2">
              {activity.slice(-8).map((e) => (
                <li
                  key={`${e.project_id}-${e.seq}`}
                  className="truncate font-mono text-2xs text-fg-faint"
                >
                  <span className="text-fg-subtle">{e.event}</span>
                  {e.stage ? ` · ${e.stage}` : ''}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      <form ref={formRef} onSubmit={onSubmit} className="shrink-0 border-t border-edge p-2.5">
        {/* One field: the send control lives inside the composer's frame, so the pair reads as a
            single object and the focus ring wraps both. */}
        <div className="flex items-end gap-1.5 rounded-xl border border-edge-strong bg-surface-sunken/50 p-1.5 transition-[border-color,box-shadow] duration-150 focus-within:border-brand/60 focus-within:shadow-[0_0_0_3px_rgb(var(--ff-brand)/0.15)]">
          <textarea
            ref={composerRef}
            aria-label="Message"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onComposerKeyDown}
            placeholder={`Refine ${STAGE_LABELS[activeStage]}…`}
            rows={2}
            className="min-h-[2.5rem] flex-1 resize-none bg-transparent px-2 py-1 text-[13px] leading-relaxed text-fg outline-none placeholder:text-fg-faint focus-visible:shadow-none"
          />
          <Button
            type="submit"
            aria-label="Send"
            loading={send.isPending}
            disabled={!draft.trim()}
            className="h-8 w-8 shrink-0 rounded-lg px-0"
          >
            {send.isPending ? null : <ArrowUp aria-hidden className="h-4 w-4" strokeWidth={2.25} />}
          </Button>
        </div>
        <p
          className={cn(
            'mt-1.5 items-center gap-1 overflow-hidden whitespace-nowrap px-1 text-2xs text-fg-faint [&>*]:shrink-0',
            narrow ? 'hidden' : 'flex',
          )}
        >
          <Kbd>Enter</Kbd> send
          <span aria-hidden className="mx-0.5">
            ·
          </span>
          <Kbd>Shift</Kbd>
          <span aria-hidden>+</span>
          <Kbd>Enter</Kbd> new line
        </p>
      </form>
    </div>
  );
}
