import { useEffect } from 'react';

import { Badge, Button, Panel } from '../../components/ui';
import { apiFetch } from '../../lib/apiClient';
import { useAuthStore } from '../../lib/stores/authStore';
import { useRealtimeStore } from '../../lib/stores/realtimeStore';

/** Dev demo (route: /_realtime) — proves ordered arrival + resume over the WS hub. */
export default function RealtimeDemo(): JSX.Element {
  const user = useAuthStore((s) => s.user);
  const status = useRealtimeStore((s) => s.status);
  const events = useRealtimeStore((s) => s.events);
  const connect = useRealtimeStore((s) => s.connect);
  const disconnect = useRealtimeStore((s) => s.disconnect);
  const dropSocket = useRealtimeStore((s) => s.dropSocket);
  const clearEvents = useRealtimeStore((s) => s.clearEvents);

  const channel = user ? `demo-${user.id}` : '';

  useEffect(() => {
    if (!channel) return;
    connect(channel);
    return () => disconnect();
  }, [channel, connect, disconnect]);

  async function startStream(): Promise<void> {
    clearEvents();
    await apiFetch('/realtime/demo/progress?steps=10&delay=0.4', { method: 'POST' });
  }

  const tone = status === 'open' ? 'success' : status === 'connecting' ? 'warning' : 'neutral';

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-xl font-semibold text-fg">Realtime demo</h1>
        <Badge tone={tone}>{status}</Badge>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void startStream()} disabled={status !== 'open'}>
          Start progress stream
        </Button>
        <Button variant="secondary" onClick={dropSocket} disabled={status !== 'open'}>
          Simulate drop (auto-resumes)
        </Button>
      </div>

      <Panel title={`Events (${events.length})`}>
        {events.length === 0 ? (
          <p className="text-sm text-fg-muted">No events yet. Start the stream above.</p>
        ) : (
          <ul className="space-y-1">
            {events.map((e) => (
              <li key={e.seq} className="font-mono text-xs text-fg-muted">
                #{e.seq} {e.event} — {JSON.stringify(e.payload)}
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
