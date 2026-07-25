import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { RealtimeEvent } from '../../lib/wsClient';
import { ChatPanel } from './ChatPanel';

const PROJECT = 'p1';

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function tokenEvent(seq: number, token: string): RealtimeEvent {
  return {
    event: 'agent.token',
    project_id: PROJECT,
    stage: 'build',
    payload: { token },
    seq,
    ts: '',
  };
}

beforeEach(() => {
  useRealtimeStore.setState({ channel: PROJECT, events: [] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  useRealtimeStore.setState({ channel: null, events: [] });
});

describe('ChatPanel', () => {
  it('posts a refine intent for the active stage when a message is sent', async () => {
    const fetchMock = vi.fn<typeof fetch>((input) => {
      const url = String(input);
      if (url.includes('/messages')) return Promise.resolve(jsonResponse([]));
      if (url.includes('/intent')) {
        return Promise.resolve(jsonResponse({ stage: 'build', to_status: 'in_progress' }));
      }
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ChatPanel projectId={PROJECT} activeStage="build" />, { wrapper: wrapper() });

    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'go build' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => {
      const intentCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/intent'));
      expect(intentCall).toBeTruthy();
      const body = JSON.parse(String((intentCall?.[1] as RequestInit).body));
      expect(body).toMatchObject({ stage: 'build', action: 'refine', message: 'go build' });
    });
  });

  it('refreshes the active stage panel, not just messages, after an intent lands', async () => {
    // The bug this pins: a design refine sent from the chat produced a new version that stayed
    // invisible, because only `messages` + `stages` were invalidated.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse([]))),
    );

    render(<ChatPanel projectId={PROJECT} activeStage="design" />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    });

    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'add dark mode' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));

    await waitFor(() => expect(invalidate).toHaveBeenCalled());
    const predicate = invalidate.mock.calls
      .map(([arg]) => (arg as { predicate?: (q: { queryKey: unknown[] }) => boolean })?.predicate)
      .find(Boolean);
    expect(predicate).toBeTypeOf('function');
    // Everything scoped to this project refreshes — including the design version list.
    expect(predicate?.({ queryKey: ['design-versions', PROJECT] })).toBe(true);
    expect(predicate?.({ queryKey: ['artifacts', PROJECT] })).toBe(true);
    expect(predicate?.({ queryKey: ['design-versions', 'another-project'] })).toBe(false);
  });

  it('renders streamed agent tokens from the realtime channel', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse([]))),
    );

    render(<ChatPanel projectId={PROJECT} activeStage="build" />, { wrapper: wrapper() });

    useRealtimeStore.setState({
      channel: PROJECT,
      events: [tokenEvent(1, 'Hello '), tokenEvent(2, 'world')],
    });

    await waitFor(() => {
      expect(screen.getByTestId('chat-stream')).toHaveTextContent('Hello world');
    });
  });

  // A long streamed answer (a build plan runs to hundreds of tokens) used to grow the column and
  // push the composer out of the panel entirely. The transcript must absorb it by scrolling.
  it('keeps the composer reachable while a long plan streams in', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(jsonResponse([]))),
    );

    render(<ChatPanel projectId={PROJECT} activeStage="build" />, { wrapper: wrapper() });

    useRealtimeStore.setState({
      channel: PROJECT,
      events: Array.from({ length: 400 }, (_, i) => tokenEvent(i + 1, `plan line ${i}\n`)),
    });

    await waitFor(() => expect(screen.getByTestId('chat-stream')).toBeInTheDocument());

    // Both controls still render, and the streamed text lives inside the scrollable transcript
    // rather than in a sibling that competes with them for height.
    expect(screen.getByLabelText('Message')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Send' })).toBeInTheDocument();

    const transcript = screen.getByTestId('chat-messages');
    expect(transcript).toContainElement(screen.getByTestId('chat-stream'));
    expect(transcript.className).toContain('overflow-auto');
    expect(transcript.className).toContain('min-h-0');
  });
});
