import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { act } from 'react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { CollectionDto, DbInfoDto, DocumentPageDto } from '../../lib/types';
import { DataBrowser } from './DataBrowser';
import { DeleteConfirm, DocEditor } from './DocEditor';

const PROJECT = 'p1';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const COLLECTIONS: CollectionDto[] = [{ name: 'todos', count: 1 }];
const INFO: DbInfoDto = { mode: 'platform', db_name: 'forgeflow_app_abc', managed: true };
const DOC = { _id: 't1', title: 'first', done: false };
const PAGE: DocumentPageDto = { documents: [DOC], total: 1, page: 1, limit: 50, pages: 1 };

function wrapper(): ({ children }: { children: ReactNode }) => JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe('DocEditor', () => {
  it('seeds the editor with the document as formatted JSON', () => {
    render(
      <DocEditor
        open
        mode="edit"
        collection="todos"
        document={DOC}
        onSave={() => {}}
        onClose={() => {}}
      />,
    );

    const editor = screen.getByTestId('editor-json') as HTMLTextAreaElement;
    expect(JSON.parse(editor.value)).toEqual(DOC);
  });

  it('saves the parsed document', () => {
    const onSave = vi.fn();
    render(<DocEditor open mode="create" collection="todos" onSave={onSave} onClose={() => {}} />);

    fireEvent.change(screen.getByTestId('editor-json'), {
      target: { value: '{"title": "new todo", "done": false}' },
    });
    fireEvent.click(screen.getByTestId('editor-save'));

    expect(onSave).toHaveBeenCalledWith({ title: 'new todo', done: false });
  });

  it('refuses malformed JSON before it reaches the API', () => {
    const onSave = vi.fn();
    render(<DocEditor open mode="create" collection="todos" onSave={onSave} onClose={() => {}} />);

    fireEvent.change(screen.getByTestId('editor-json'), { target: { value: '{ not json' } });
    fireEvent.click(screen.getByTestId('editor-save'));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId('editor-json-error')).toBeInTheDocument();
  });

  it('refuses a JSON value that is not a document object', () => {
    const onSave = vi.fn();
    render(<DocEditor open mode="create" collection="todos" onSave={onSave} onClose={() => {}} />);

    fireEvent.change(screen.getByTestId('editor-json'), { target: { value: '[1, 2, 3]' } });
    fireEvent.click(screen.getByTestId('editor-save'));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByTestId('editor-json-error')).toHaveTextContent('must be a JSON object');
  });

  it('surfaces an API guardrail rejection next to the document', () => {
    render(
      <DocEditor
        open
        mode="create"
        collection="todos"
        error="The $where operator is not allowed"
        onSave={() => {}}
        onClose={() => {}}
      />,
    );

    expect(screen.getByTestId('editor-api-error')).toHaveTextContent('$where');
  });
});

describe('DeleteConfirm', () => {
  it('names what is about to be destroyed and that it cannot be undone', () => {
    render(
      <DeleteConfirm open collection="todos" docId="t1" onConfirm={() => {}} onClose={() => {}} />,
    );

    const warning = screen.getByTestId('delete-warning');
    expect(warning).toHaveTextContent('t1');
    expect(warning).toHaveTextContent('todos');
    expect(warning).toHaveTextContent('cannot be undone');
  });

  it('only deletes on explicit confirmation', () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(
      <DeleteConfirm open collection="todos" docId="t1" onConfirm={onConfirm} onClose={onClose} />,
    );

    fireEvent.click(screen.getByTestId('delete-cancel'));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('delete-confirm'));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe('DataBrowser CRUD flows', () => {
  let calls: { method: string; url: string; body: unknown }[] = [];

  beforeEach(() => {
    calls = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });

      if (method === 'POST') return Promise.resolve(json({ _id: 'new1', title: 'created' }, 201));
      if (method === 'PUT') return Promise.resolve(json({ _id: 't1', title: 'edited' }));
      if (method === 'DELETE') return Promise.resolve(json({ deleted: true }));
      if (url.includes('/data/info')) return Promise.resolve(json(INFO));
      if (url.includes('/data/collections/')) return Promise.resolve(json(PAGE));
      if (url.includes('/data/collections')) return Promise.resolve(json(COLLECTIONS));
      return Promise.resolve(json({}));
    });
  });

  afterEach(() => vi.restoreAllMocks());

  async function renderBrowser(): Promise<void> {
    render(<DataBrowser projectId={PROJECT} />, { wrapper: wrapper() });
    await act(async () => {
      for (let i = 0; i < 4; i += 1) await new Promise((r) => setTimeout(r, 0));
    });
    await screen.findByTestId('doc-table');
  }

  it('creates a document end to end', async () => {
    await renderBrowser();

    fireEvent.click(screen.getByTestId('doc-create'));
    fireEvent.change(screen.getByTestId('editor-json'), {
      target: { value: '{"title": "created"}' },
    });
    fireEvent.click(screen.getByTestId('editor-save'));

    await waitFor(() => {
      const post = calls.find((c) => c.method === 'POST');
      expect(post?.url).toContain('/data/collections/todos/docs');
      expect(post?.body).toEqual({ document: { title: 'created' } });
    });
  });

  it('edits an existing document from its row', async () => {
    await renderBrowser();

    fireEvent.click(screen.getByTestId('view-t1'));
    const editor = screen.getByTestId('editor-json') as HTMLTextAreaElement;
    expect(JSON.parse(editor.value)).toEqual(DOC); // round-trips the whole document, `_id` included

    fireEvent.change(editor, { target: { value: '{"_id": "t1", "title": "edited"}' } });
    fireEvent.click(screen.getByTestId('editor-save'));

    await waitFor(() => {
      const put = calls.find((c) => c.method === 'PUT');
      expect(put?.url).toContain('/docs/t1');
      expect(put?.body).toEqual({ document: { _id: 't1', title: 'edited' } });
    });
  });

  it('never deletes without the confirmation step', async () => {
    await renderBrowser();

    fireEvent.click(screen.getByTestId('delete-t1'));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false); // the click only opens the dialog

    fireEvent.click(screen.getByTestId('delete-cancel'));
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false);

    fireEvent.click(screen.getByTestId('delete-t1'));
    fireEvent.click(screen.getByTestId('delete-confirm'));
    await waitFor(() => {
      const del = calls.find((c) => c.method === 'DELETE');
      expect(del?.url).toContain('/docs/t1');
    });
  });

  it('keeps a rejected save on screen with the reason', async () => {
    await renderBrowser();
    vi.mocked(globalThis.fetch).mockImplementationOnce(() =>
      Promise.resolve(json({ error: { type: 'user_error', message: 'Document too large' } }, 400)),
    );

    fireEvent.click(screen.getByTestId('doc-create'));
    fireEvent.change(screen.getByTestId('editor-json'), { target: { value: '{"a": 1}' } });
    fireEvent.click(screen.getByTestId('editor-save'));

    // The editor stays open with the document intact, so the user can fix and retry.
    expect(await screen.findByTestId('editor-api-error')).toHaveTextContent('Document too large');
    expect(screen.getByTestId('editor-json')).toBeInTheDocument();
  });
});
