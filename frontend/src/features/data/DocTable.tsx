import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronsUpDown,
  Inbox,
  Trash2,
} from 'lucide-react';

import { Button, EmptyState, Skeleton } from '../../components/ui';
import type { DataDocument, DocumentPageDto } from '../../lib/types';
import { columnsOf, formatCell, nextSort, type SortState } from './table';

export function DocTable({
  page,
  loading = false,
  sort,
  onSortChange,
  onPageChange,
  onView,
  onDelete,
}: {
  page: DocumentPageDto | null;
  loading?: boolean;
  sort: SortState;
  onSortChange: (sort: SortState) => void;
  onPageChange: (page: number) => void;
  onView: (doc: DataDocument) => void;
  onDelete: (doc: DataDocument) => void;
}): JSX.Element {
  if (loading && !page) {
    return (
      <div
        className="space-y-1 rounded-lg border border-edge p-2"
        data-testid="docs-loading"
        aria-hidden
      >
        <Skeleton className="h-6 w-full" />
        {Array.from({ length: 5 }, (_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    );
  }

  if (!page || page.documents.length === 0) {
    return (
      <EmptyState
        icon={<Inbox aria-hidden strokeWidth={1.5} />}
        title="No documents"
        description="Nothing matches here yet. Create one, or clear the filter."
      />
    );
  }

  const columns = columnsOf(page.documents);

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-edge">
        <table className="w-full min-w-max text-left text-xs" data-testid="doc-table">
          <thead className="sticky top-0 bg-fg/[0.03] text-fg-subtle">
            <tr>
              {columns.map((column) => {
                const active = sort?.field === column;
                return (
                  <th key={column} className="px-3 py-2 font-medium">
                    <button
                      type="button"
                      data-testid={`sort-${column}`}
                      onClick={() => onSortChange(nextSort(sort, column))}
                      className="flex items-center gap-1 hover:text-fg"
                      aria-label={`Sort by ${column}`}
                    >
                      <span className="font-mono">{column}</span>
                      <span aria-hidden className={active ? 'text-brand-text' : 'text-fg-faint'}>
                        {active ? (
                          sort?.direction === 1 ? (
                            <ChevronUp className="h-3 w-3" strokeWidth={2} />
                          ) : (
                            <ChevronDown className="h-3 w-3" strokeWidth={2} />
                          )
                        ) : (
                          <ChevronsUpDown className="h-3 w-3" strokeWidth={2} />
                        )}
                      </span>
                    </button>
                  </th>
                );
              })}
              <th className="px-3 py-2 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {page.documents.map((doc) => (
              <tr
                key={String(doc._id)}
                className="group border-t border-edge transition-colors hover:bg-fg/[0.025]"
                data-testid={`doc-row-${String(doc._id)}`}
              >
                {columns.map((column) => (
                  <td
                    key={column}
                    className="max-w-[16rem] truncate px-3 py-1.5 font-mono text-2xs text-fg-muted first:text-fg-subtle"
                  >
                    {formatCell(doc[column])}
                  </td>
                ))}
                <td className="whitespace-nowrap px-2 py-1 text-right">
                  <Button
                    size="xs"
                    variant="ghost"
                    data-testid={`view-${String(doc._id)}`}
                    onClick={() => onView(doc)}
                  >
                    View
                  </Button>
                  <Button
                    size="xs"
                    variant="ghost"
                    data-testid={`delete-${String(doc._id)}`}
                    className="hover:bg-danger/10 hover:text-danger"
                    onClick={() => onDelete(doc)}
                  >
                    <Trash2 aria-hidden className="h-3 w-3" strokeWidth={1.75} />
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-xs text-fg-subtle">
        <span data-testid="doc-count" className="tabular-nums">
          {page.total} {page.total === 1 ? 'document' : 'documents'} · page {page.page} of{' '}
          {page.pages}
        </span>
        <div className="flex gap-1.5">
          <Button
            size="sm"
            variant="secondary"
            disabled={page.page <= 1 || loading}
            data-testid="page-prev"
            onClick={() => onPageChange(page.page - 1)}
          >
            <ChevronLeft aria-hidden className="h-3.5 w-3.5" strokeWidth={2} />
            Previous
          </Button>
          <Button
            size="sm"
            variant="secondary"
            disabled={page.page >= page.pages || loading}
            data-testid="page-next"
            onClick={() => onPageChange(page.page + 1)}
          >
            Next
            <ChevronRight aria-hidden className="h-3.5 w-3.5" strokeWidth={2} />
          </Button>
        </div>
      </div>
    </div>
  );
}

export default DocTable;
