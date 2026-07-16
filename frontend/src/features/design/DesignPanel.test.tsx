import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useToastStore } from '../../lib/stores/toastStore';
import { DesignPanel } from './DesignPanel';

const PROJECT = 'p1';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface MockState {
  versions: Array<Record<string, unknown>>;
  artifact: Record<string, unknown>;
  intentStatus: number;
  intentCalls: Array<Record<string, unknown>>;
}

function installFetch(state: MockState): void {
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    if (url.includes('/design/provider')) {
      return Promise.resolve(
        json({
          key: 'fake',
          health: 'ok',
          capabilities: {
            provider: 'fake',
            from_image: true,
            from_text: true,
            refine: true,
            fetch_code: true,
            max_images: 8,
          },
        }),
      );
    }
    if (url.includes('/design/images')) {
      return Promise.resolve(
        json({ images: [{ ref: 'r1', filename: 'a.png', media_type: 'image/png' }] }),
      );
    }
    if (url.includes('/stages')) {
      return Promise.resolve(json([{ stage: 'design', status: 'awaiting_user', artifacts: [] }]));
    }
    if (url.includes('/artifacts?')) return Promise.resolve(json(state.versions));
    if (url.includes('/artifacts/')) return Promise.resolve(json(state.artifact));
    if (url.includes('/intent')) {
      state.intentCalls.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      if (state.intentStatus >= 400) {
        return Promise.resolve(
          json(
            {
              error: {
                type: 'provider_error',
                message: 'quota exhausted',
                fallback_hint: 'switch to figma',
              },
            },
            state.intentStatus,
          ),
        );
      }
      return Promise.resolve(
        json({
          stage: 'design',
          action: method,
          from_status: 'empty',
          to_status: 'awaiting_user',
          stale: [],
          messages: [],
          artifacts: [],
          run_id: 'r',
        }),
      );
    }
    return Promise.resolve(json({}));
  });
  vi.stubGlobal('fetch', fetchMock);
}

function version(id: string, v: number, source = 'text'): Record<string, unknown> {
  return {
    id,
    project_id: PROJECT,
    stage: 'design',
    type: 'design',
    version: v,
    ref: null,
    meta: { source },
    created_at: '',
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<DesignPanel projectId={PROJECT} />, { wrapper });
}

let state: MockState;

beforeEach(() => {
  state = { versions: [], artifact: {}, intentStatus: 200, intentCalls: [] };
  useToastStore.setState({ toasts: [] });
});

afterEach(() => vi.unstubAllGlobals());

describe('DesignPanel', () => {
  it('shows the active provider health and generates from a text prompt', async () => {
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('provider-health')).toHaveTextContent('fake: ok');

    fireEvent.change(screen.getByTestId('design-prompt'), { target: { value: 'a todo app' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate from text' }));

    await waitFor(() => {
      const call = state.intentCalls.at(-1);
      expect(call).toMatchObject({
        stage: 'design',
        action: 'refine',
        payload: { text: 'a todo app' },
      });
    });
  });

  it('renders the selected version in a sandboxed preview', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = {
      ...version('a1', 1),
      content: JSON.stringify({ html: '<h1>hi</h1>', css: 'h1{}' }),
    };
    installFetch(state);
    renderPanel();

    const frame = await screen.findByTestId('design-preview-frame');
    await waitFor(() => expect(frame.getAttribute('srcdoc')).toContain('<h1>hi</h1>'));
    // Untrusted markup is fully sandboxed.
    expect(frame.getAttribute('sandbox')).toBe('');
  });

  it('refines an existing design with a chat instruction', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = { ...version('a1', 1), content: JSON.stringify({ html: '<h1/>', css: '' }) };
    installFetch(state);
    renderPanel();

    await screen.findByTestId('design-version-1'); // wait until the design has loaded (enables refine)
    const input = screen.getByTestId('design-refine-input');
    fireEvent.change(input, { target: { value: 'make the header violet' } });
    fireEvent.click(screen.getByRole('button', { name: 'Refine' }));

    await waitFor(() => {
      expect(state.intentCalls.at(-1)).toMatchObject({
        action: 'refine',
        message: 'make the header violet',
      });
    });
  });

  it('approves and skips via the conductor', async () => {
    state.versions = [version('a1', 1)];
    state.artifact = { ...version('a1', 1), content: JSON.stringify({ html: '<h1/>', css: '' }) };
    installFetch(state);
    renderPanel();

    await screen.findByTestId('design-version-1'); // wait until the design has loaded (enables approve)
    fireEvent.click(screen.getByTestId('design-approve'));
    await waitFor(() => expect(state.intentCalls.at(-1)).toMatchObject({ action: 'proceed' }));

    fireEvent.click(screen.getByTestId('design-skip'));
    await waitFor(() => expect(state.intentCalls.at(-1)).toMatchObject({ action: 'skip' }));
  });

  it('imports a bring-your-own design without a provider call', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('design-import-toggle'));
    fireEvent.change(screen.getByTestId('design-own-html'), { target: { value: '<h1>mine</h1>' } });
    fireEvent.click(screen.getByRole('button', { name: 'Import' }));

    await waitFor(() => {
      expect(state.intentCalls.at(-1)).toMatchObject({
        action: 'refine',
        payload: { own_design: { html: '<h1>mine</h1>', css: '' } },
      });
    });
  });

  it('surfaces a provider error with its fallback hint', async () => {
    state.intentStatus = 502;
    installFetch(state);
    renderPanel();

    fireEvent.change(await screen.findByTestId('design-prompt'), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate from text' }));

    await waitFor(() => {
      const toasts = useToastStore.getState().toasts;
      expect(toasts.some((t) => t.description?.includes('switch to figma'))).toBe(true);
    });
  });
});
