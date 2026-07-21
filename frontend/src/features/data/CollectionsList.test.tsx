import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { act } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CollectionDto, DbInfoDto, DocumentPageDto } from '../../lib/types';
import { CollectionsList } from './CollectionsList';
import { DataBrowser } from './DataBrowser';

const PROJECT = 'p1';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const COLLECTIONS: CollectionDto[] = [
  { name: 'todos', count: 12 },
  { name: 'users', count: 3 },
];

const INFO: DbInfoDto = { mode: 'platform', db_name: 'forgeflow_app_abc123', managed: true };

function pageOf(docs: Record<string, unknown>[]): DocumentPageDto {
  return { documents: docs, total: docs.length, page: 1, limit: 50, pages: 1 };
}

function wrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('CollectionsList', () => {
  it('lists collections with their document counts', () => {
    render(<CollectionsList collections={COLLECTIONS} selected={null} onSelect={() => {}} />, {
      wrapper: wrapper(),
    });

    expect(screen.getByTestId('collection-todos')).toHaveTextContent('todos');
    expect(screen.getByTestId('collection-todos')).toHaveTextContent('12');
    expect(screen.getByTestId('collection-users')).toHaveTextContent('users');
  });

  it('marks the selected collection', () => {
    render(<CollectionsList collections={COLLECTIONS} selected="users" onSelect={() => {}} />, {
      wrapper: wrapper(),
    });

    expect(screen.getByTestId('collection-users')).toHaveAttribute('aria-current', 'true');
    expect(screen.getByTestId('collection-todos')).not.toHaveAttribute('aria-current');
  });

  it('reports the collection the user picked', () => {
    const onSelect = vi.fn();
    render(<CollectionsList collections={COLLECTIONS} selected={null} onSelect={onSelect} />, {
      wrapper: wrapper(),
    });

    fireEvent.click(screen.getByTestId('collection-users'));
    expect(onSelect).toHaveBeenCalledWith('users');
  });

  it('explains an empty database rather than showing a blank list', () => {
    render(<CollectionsList collections={[]} selected={null} onSelect={() => {}} />, {
      wrapper: wrapper(),
    });

    expect(screen.getByText('No collections yet')).toBeInTheDocument();
  });
});

describe('DataBrowser wiring', () => {
  let requests: string[] = [];

  beforeEach(() => {
    requests = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input);
      requests.push(url);
      if (url.includes('/data/info')) return Promise.resolve(json(INFO));
      if (url.includes('/data/collections/')) {
        const docs = url.includes('users')
          ? [{ _id: 'u1', email: 'a@b.test' }]
          : [{ _id: 't1', title: 'first todo' }];
        return Promise.resolve(json(pageOf(docs)));
      }
      if (url.includes('/data/collections')) return Promise.resolve(json(COLLECTIONS));
      return Promise.resolve(json({}));
    });
  });

  afterEach(() => vi.restoreAllMocks());

  /** Collections load, an effect picks the first one, then its documents load — three hops. */
  async function settle(): Promise<void> {
    await act(async () => {
      for (let i = 0; i < 4; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 0));
      }
    });
  }

  async function renderBrowser(): Promise<void> {
    render(<DataBrowser projectId={PROJECT} />, { wrapper: wrapper() });
    await settle();
  }

  it('selects the first collection and loads its documents', async () => {
    await renderBrowser();

    expect(screen.getByTestId('collection-todos')).toHaveAttribute('aria-current', 'true');
    expect(await screen.findByTestId('doc-table')).toHaveTextContent('first todo');
  });

  it('loads the documents of a collection the user selects', async () => {
    await renderBrowser();

    fireEvent.click(screen.getByTestId('collection-users'));
    await settle();

    expect(requests.some((u) => u.includes('/collections/users/docs'))).toBe(true);
    expect(await screen.findByTestId('doc-table')).toHaveTextContent('a@b.test');
  });

  it('always shows which database is being edited', async () => {
    await renderBrowser();

    const scope = screen.getByTestId('data-scope');
    expect(scope).toHaveTextContent('project database');
    expect(scope).toHaveTextContent('forgeflow_app_abc123');
  });

  it('flags a BYO database differently, since it is the user’s own', async () => {
    vi.mocked(globalThis.fetch).mockImplementation((input) => {
      const url = String(input);
      if (url.includes('/data/info')) {
        return Promise.resolve(json({ mode: 'byo', db_name: 'my_prod_db', managed: false }));
      }
      if (url.includes('/data/collections/')) return Promise.resolve(json(pageOf([])));
      if (url.includes('/data/collections')) return Promise.resolve(json(COLLECTIONS));
      return Promise.resolve(json({}));
    });
    await renderBrowser();

    expect(screen.getByTestId('data-scope')).toHaveTextContent('your database');
    expect(screen.getByTestId('data-scope')).toHaveTextContent('my_prod_db');
  });
});
