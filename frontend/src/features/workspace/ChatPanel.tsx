import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Button } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import { toast } from '../../lib/stores/toastStore';
import type { MessageDto, MessageRole, Stage } from '../../lib/types';
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
  const streamed = useMemo(
    () =>
      activity
        .filter((e) => e.event === 'agent.token')
        .map((e) => String(e.payload.token ?? e.payload.text ?? ''))
        .join(''),
    [activity],
  );

  const send = useMutation({
    mutationFn: (message: string) =>
      submitIntent(projectId, { stage: activeStage, action: 'refine', message }),
    onSuccess: () => {
      setDraft('');
      void queryClient.invalidateQueries({ queryKey: ['messages', projectId] });
      void queryClient.invalidateQueries({ queryKey: ['stages', projectId] });
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

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-slate-800 px-4 py-2 text-xs uppercase tracking-wide text-slate-500">
        {STAGE_LABELS[activeStage]} conversation
      </div>

      <div data-testid="chat-messages" className="flex flex-1 flex-col gap-2 overflow-auto p-4">
        {messages.length === 0 ? (
          <p className="text-sm text-slate-500">No messages yet. Say something to get started.</p>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${ROLE_STYLES[m.role]}`}
            >
              {m.content}
            </div>
          ))
        )}
      </div>

      {activity.length > 0 ? (
        <div data-testid="chat-activity" className="border-t border-slate-800 px-4 py-2">
          <p className="mb-1 text-xs uppercase tracking-wide text-slate-500">Activity</p>
          {streamed ? (
            <p data-testid="chat-stream" className="font-mono text-xs text-slate-300">
              {streamed}
            </p>
          ) : null}
          <ul className="max-h-24 space-y-0.5 overflow-auto">
            {activity.slice(-8).map((e) => (
              <li key={`${e.project_id}-${e.seq}`} className="text-xs text-slate-500">
                <span className="text-slate-400">{e.event}</span>
                {e.stage ? ` · ${e.stage}` : ''}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <form onSubmit={onSubmit} className="flex items-end gap-2 border-t border-slate-800 p-3">
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
