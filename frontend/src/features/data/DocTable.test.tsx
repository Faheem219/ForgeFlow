import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { DocumentPageDto } from '../../lib/types';
import { DocTable } from './DocTable';
import { columnsOf, formatCell } from './table';
import { buildFilter, coerceValue, type FilterDraft } from './filters';

function pageOf(
  docs: Record<string, unknown>[],
  overrides: Partial<DocumentPageDto> = {},
): DocumentPageDto {
  return {
    documents: docs,
    total: docs.length,
    page: 1,
    limit: 50,
    pages: 1,
    ...overrides,
  };
}

const DOCS = [
  { _id: 't1', title: 'first', done: false, order: 1 },
  { _id: 't2', title: 'second', done: true, order: 2 },
];

function noop(): void {}

describe('columns', () => {
  it('derives columns from the documents, with _id first', () => {
    expect(columnsOf(DOCS)).toEqual(['_id', 'title', 'done', 'order']);
  });

  it('unions fields across documents, since generated apps have no schema', () => {
    const ragged = [
      { _id: '1', a: 1 },
      { _id: '2', b: 2 },
    ];
    expect(columnsOf(ragged)).toEqual(['_id', 'a', 'b']);
  });

  it('renders values compactly', () => {
    expect(formatCell(null)).toBe('null');
    expect(formatCell(undefined)).toBe('—');
    expect(formatCell(false)).toBe('false');
    expect(formatCell({ a: 1 })).toBe('{"a":1}');
    expect(formatCell(['x'])).toBe('["x"]');
  });
});

describe('DocTable', () => {
  it('renders a row per document with its fields', () => {
    render(
      <DocTable
        page={pageOf(DOCS)}
        sort={null}
        onSortChange={noop}
        onPageChange={noop}
        onView={noop}
        onDelete={noop}
      />,
    );

    expect(screen.getByTestId('doc-row-t1')).toHaveTextContent('first');
    expect(screen.getByTestId('doc-row-t2')).toHaveTextContent('second');
    expect(screen.getByTestId('doc-count')).toHaveTextContent('2 documents');
  });

  it('cycles a column through ascending, descending and unsorted', () => {
    const onSortChange = vi.fn();
    const { rerender } = render(
      <DocTable
        page={pageOf(DOCS)}
        sort={null}
        onSortChange={onSortChange}
        onPageChange={noop}
        onView={noop}
        onDelete={noop}
      />,
    );

    fireEvent.click(screen.getByTestId('sort-order'));
    expect(onSortChange).toHaveBeenLastCalledWith({ field: 'order', direction: 1 });

    rerender(
      <DocTable
        page={pageOf(DOCS)}
        sort={{ field: 'order', direction: 1 }}
        onSortChange={onSortChange}
        onPageChange={noop}
        onView={noop}
        onDelete={noop}
      />,
    );
    fireEvent.click(screen.getByTestId('sort-order'));
    expect(onSortChange).toHaveBeenLastCalledWith({ field: 'order', direction: -1 });

    rerender(
      <DocTable
        page={pageOf(DOCS)}
        sort={{ field: 'order', direction: -1 }}
        onSortChange={onSortChange}
        onPageChange={noop}
        onView={noop}
        onDelete={noop}
      />,
    );
    fireEvent.click(screen.getByTestId('sort-order'));
    expect(onSortChange).toHaveBeenLastCalledWith(null); // a third click clears it
  });

  it('pages forwards and backwards within bounds', () => {
    const onPageChange = vi.fn();
    render(
      <DocTable
        page={pageOf(DOCS, { page: 2, pages: 3, total: 120 })}
        sort={null}
        onSortChange={noop}
        onPageChange={onPageChange}
        onView={noop}
        onDelete={noop}
      />,
    );

    fireEvent.click(screen.getByTestId('page-next'));
    expect(onPageChange).toHaveBeenLastCalledWith(3);
    fireEvent.click(screen.getByTestId('page-prev'));
    expect(onPageChange).toHaveBeenLastCalledWith(1);
  });

  it('disables paging at the ends', () => {
    render(
      <DocTable
        page={pageOf(DOCS, { page: 1, pages: 1 })}
        sort={null}
        onSortChange={noop}
        onPageChange={noop}
        onView={noop}
        onDelete={noop}
      />,
    );

    expect(screen.getByTestId('page-prev')).toBeDisabled();
    expect(screen.getByTestId('page-next')).toBeDisabled();
  });

  it('offers view and delete per row', () => {
    const onView = vi.fn();
    const onDelete = vi.fn();
    render(
      <DocTable
        page={pageOf(DOCS)}
        sort={null}
        onSortChange={noop}
        onPageChange={noop}
        onView={onView}
        onDelete={onDelete}
      />,
    );

    fireEvent.click(screen.getByTestId('view-t1'));
    fireEvent.click(screen.getByTestId('delete-t2'));
    expect(onView).toHaveBeenCalledWith(DOCS[0]);
    expect(onDelete).toHaveBeenCalledWith(DOCS[1]);
  });

  it('explains an empty result rather than showing a bare table', () => {
    render(
      <DocTable
        page={pageOf([])}
        sort={null}
        onSortChange={noop}
        onPageChange={noop}
        onView={noop}
        onDelete={noop}
      />,
    );

    expect(screen.getByText('No documents')).toBeInTheDocument();
    expect(screen.queryByTestId('doc-table')).not.toBeInTheDocument();
  });
});

describe('filter translation', () => {
  function draft(over: Partial<FilterDraft>): FilterDraft {
    return { field: 'title', op: 'eq', value: '', ...over };
  }

  it('coerces typed values so numbers and booleans behave as expected', () => {
    expect(coerceValue('42')).toBe(42);
    expect(coerceValue('true')).toBe(true);
    expect(coerceValue('null')).toBeNull();
    expect(coerceValue('hello')).toBe('hello');
  });

  it('builds equality and comparison filters', () => {
    expect(buildFilter(draft({ op: 'eq', value: 'first' }))).toEqual({ title: 'first' });
    expect(buildFilter(draft({ field: 'order', op: 'gte', value: '2' }))).toEqual({
      order: { $gte: 2 },
    });
    expect(buildFilter(draft({ op: 'ne', value: 'x' }))).toEqual({ title: { $ne: 'x' } });
  });

  it('escapes "contains" so user text is matched literally, not as a regex', () => {
    expect(buildFilter(draft({ op: 'contains', value: 'a.b*c' }))).toEqual({
      title: { $regex: 'a\\.b\\*c', $options: 'i' },
    });
  });

  it('supports exists without a value', () => {
    expect(buildFilter(draft({ op: 'exists', value: '' }))).toEqual({ title: { $exists: true } });
    expect(buildFilter(draft({ op: 'exists', value: 'false' }))).toEqual({
      title: { $exists: false },
    });
  });

  it('returns nothing when the draft is incomplete', () => {
    expect(buildFilter(draft({ field: '' }))).toBeNull();
    expect(buildFilter(draft({ op: 'eq', value: '' }))).toBeNull();
  });

  it('only ever emits operators the API allows', () => {
    // Every operator the bar can produce is on the backend allowlist (phase-41), so ordinary
    // filtering can never trip a guardrail.
    const allowed = new Set(['$ne', '$regex', '$options', '$gt', '$gte', '$lt', '$lte', '$exists']);
    const ops: FilterDraft['op'][] = ['eq', 'ne', 'contains', 'gt', 'gte', 'lt', 'lte', 'exists'];

    for (const op of ops) {
      const built = buildFilter(draft({ op, value: '1' })) ?? {};
      const value = built.title;
      if (value && typeof value === 'object') {
        for (const key of Object.keys(value)) expect(allowed.has(key)).toBe(true);
      }
    }
  });
});
