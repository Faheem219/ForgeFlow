import { Button, EmptyState, Spinner } from '../../components/ui';
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
      <div className="flex justify-center py-8" data-testid="docs-loading">
        <Spinner />
      </div>
    );
  }

  if (!page || page.documents.length === 0) {
    return (
      <EmptyState
        title="No documents"
        description="Nothing matches here yet. Create one, or clear the filter."
      />
    );
  }

  const columns = columnsOf(page.documents);

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full min-w-max text-left text-xs" data-testid="doc-table">
          <thead className="bg-slate-900/60 text-slate-400">
            <tr>
              {columns.map((column) => {
                const active = sort?.field === column;
                return (
                  <th key={column} className="px-2 py-1.5 font-medium">
                    <button
                      type="button"
                      data-testid={`sort-${column}`}
                      onClick={() => onSortChange(nextSort(sort, column))}
                      className="flex items-center gap-1 hover:text-slate-200"
                      aria-label={`Sort by ${column}`}
                    >
                      <span className="font-mono">{column}</span>
                      <span aria-hidden className={active ? 'text-brand-300' : 'text-slate-700'}>
                        {active ? (sort?.direction === 1 ? '▲' : '▼') : '↕'}
                      </span>
                    </button>
                  </th>
                );
              })}
              <th className="px-2 py-1.5 text-right font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {page.documents.map((doc) => (
              <tr
                key={String(doc._id)}
                className="border-t border-slate-800/60 hover:bg-slate-900/40"
                data-testid={`doc-row-${String(doc._id)}`}
              >
                {columns.map((column) => (
                  <td key={column} className="max-w-[16rem] truncate px-2 py-1.5 text-slate-300">
                    {formatCell(doc[column])}
                  </td>
                ))}
                <td className="whitespace-nowrap px-2 py-1.5 text-right">
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid={`view-${String(doc._id)}`}
                    onClick={() => onView(doc)}
                  >
                    View
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid={`delete-${String(doc._id)}`}
                    onClick={() => onDelete(doc)}
                  >
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-xs text-slate-500">
        <span data-testid="doc-count">
          {page.total} {page.total === 1 ? 'document' : 'documents'} · page {page.page} of{' '}
          {page.pages}
        </span>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={page.page <= 1 || loading}
            data-testid="page-prev"
            onClick={() => onPageChange(page.page - 1)}
          >
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
          </Button>
        </div>
      </div>
    </div>
  );
}

export default DocTable;
