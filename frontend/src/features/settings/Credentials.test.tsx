import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { Credentials } from './Credentials';

const SECRET = 'vercel_live_tok_supersecret_42';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface Stored {
  kind: string;
  scope: string;
  created_at: string;
  last4: string | null;
}

interface MockState {
  credentials: Stored[];
  puts: Array<{ url: string; body: unknown }>;
  deletes: string[];
}

function installFetch(state: MockState): void {
  const fetchMock = vi.fn<typeof fetch>((input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';

    if (url.endsWith('/credentials') && method === 'GET') {
      return Promise.resolve(json(state.credentials));
    }
    if (method === 'PUT') {
      const body = JSON.parse(String(init?.body)) as { secret: string };
      state.puts.push({ url, body });
      const kind = url.split('/').pop() as string;
      // The server only ever answers with metadata — never the value it was given.
      const saved: Stored = {
        kind,
        scope: 'byo',
        created_at: '2026-07-20T00:00:00Z',
        last4: body.secret.slice(-4),
      };
      state.credentials = [...state.credentials.filter((c) => c.kind !== kind), saved];
      return Promise.resolve(json(saved));
    }
    if (method === 'DELETE') {
      const kind = url.split('/').pop() as string;
      state.deletes.push(kind);
      state.credentials = state.credentials.filter((c) => c.kind !== kind);
      return Promise.resolve(json({ deleted: true }));
    }
    return Promise.resolve(json({}));
  });
  vi.stubGlobal('fetch', fetchMock);
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<Credentials />, { wrapper });
}

let state: MockState;

beforeEach(() => {
  state = { credentials: [], puts: [], deletes: [] };
});

afterEach(() => vi.unstubAllGlobals());

describe('Credentials', () => {
  it('adds a BYO token and clears the input afterwards', async () => {
    installFetch(state);
    renderPanel();

    const input = (await screen.findByTestId('credential-secret')) as HTMLInputElement;
    fireEvent.change(input, { target: { value: SECRET } });
    fireEvent.click(screen.getByTestId('credential-save'));

    await waitFor(() => expect(state.puts).toHaveLength(1));
    expect(state.puts[0].url).toContain('/credentials/vercel');
    expect(state.puts[0].body).toEqual({ secret: SECRET });
    // The plaintext must not linger in the form once it has been stored.
    await waitFor(() => expect(input.value).toBe(''));
  });

  it('lets the user choose which provider the token belongs to', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.change(await screen.findByTestId('credential-kind'), { target: { value: 'render' } });
    fireEvent.change(screen.getByTestId('credential-secret'), { target: { value: SECRET } });
    fireEvent.click(screen.getByTestId('credential-save'));

    await waitFor(() => expect(state.puts[0].url).toContain('/credentials/render'));
  });

  it('never renders a stored value — only metadata and the last four characters', async () => {
    state.credentials = [
      { kind: 'vercel', scope: 'byo', created_at: '2026-07-20T00:00:00Z', last4: SECRET.slice(-4) },
    ];
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('credential-row-vercel')).toBeInTheDocument();
    expect(screen.getByText(/····.{4}/)).toBeInTheDocument();
    expect(screen.getByText('byo')).toBeInTheDocument();
    // The value itself appears nowhere in the rendered document.
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it('keeps the secret out of the DOM after saving it', async () => {
    installFetch(state);
    renderPanel();

    fireEvent.change(await screen.findByTestId('credential-secret'), {
      target: { value: SECRET },
    });
    fireEvent.click(screen.getByTestId('credential-save'));

    await waitFor(() => expect(screen.getByTestId('credential-row-vercel')).toBeInTheDocument());
    expect(document.body.textContent).not.toContain(SECRET);
    expect(document.body.innerHTML).not.toContain(SECRET);
  });

  it('deletes a stored token', async () => {
    state.credentials = [
      { kind: 'figma', scope: 'byo', created_at: '2026-07-20T00:00:00Z', last4: 'abcd' },
    ];
    installFetch(state);
    renderPanel();

    fireEvent.click(await screen.findByTestId('credential-delete-figma'));

    await waitFor(() => expect(state.deletes).toEqual(['figma']));
    await waitFor(() =>
      expect(screen.queryByTestId('credential-row-figma')).not.toBeInTheDocument(),
    );
  });

  it('masks the token input', async () => {
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('credential-secret')).toHaveAttribute('type', 'password');
  });

  it('will not submit an empty token', async () => {
    installFetch(state);
    renderPanel();

    expect(await screen.findByTestId('credential-save')).toBeDisabled();
    expect(state.puts).toHaveLength(0);
  });
});
