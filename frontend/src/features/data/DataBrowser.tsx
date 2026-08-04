import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { Badge, Button } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { toast } from '../../lib/stores/toastStore';
import type { DataDocument } from '../../lib/types';
import { CollectionsList } from './CollectionsList';
import { DeleteConfirm, DocEditor } from './DocEditor';
import { DocTable } from './DocTable';
import { columnsOf, type SortState } from './table';
import { FilterBar } from './FilterBar';
import {
  createDocument,
  deleteDocument,
  getDbInfo,
  listCollections,
  listDocuments,
  updateDocument,
} from './api';
import { EMPTY_FILTER, buildFilter, type FilterDraft } from './filters';

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/**
 * Manage a generated app's data without leaving ForgeFlow or opening Atlas (D8).
 *
 * Everything here is scoped by the project in the URL — the API derives the database from it and
 * accepts no database name at all (phase-41), so there is no "wrong project" this UI could reach.
 * The scope banner keeps that visible rather than implied: destructive actions on live app data
 * should never be taken while unsure which database you are in.
 */
export function DataBrowser({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [collection, setCollection] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState<SortState>(null);
  const [draft, setDraft] = useState<FilterDraft>(EMPTY_FILTER);
  const [applied, setApplied] = useState<Record<string, unknown> | null>(null);
  const [editing, setEditing] = useState<{
    mode: 'create' | 'edit';
    doc: DataDocument | null;
  } | null>(null);
  const [pendingDelete, setPendingDelete] = useState<DataDocument | null>(null);
  const [editorError, setEditorError] = useState<string | null>(null);

  const infoQuery = useQuery({
    queryKey: ['data-info', projectId],
    queryFn: () => getDbInfo(projectId),
  });
  const collectionsQuery = useQuery({
    queryKey: ['data-collections', projectId],
    queryFn: () => listCollections(projectId),
  });

  const collections = useMemo(() => collectionsQuery.data ?? [], [collectionsQuery.data]);

  // Land on the first collection so the panel is never an empty stare.
  useEffect(() => {
    if (!collection && collections.length > 0) setCollection(collections[0].name);
  }, [collections, collection]);

  const docsQuery = useQuery({
    queryKey: ['data-docs', projectId, collection, applied, sort, page],
    queryFn: () =>
      listDocuments(projectId, collection as string, {
        filter: applied,
        sort: sort ? { [sort.field]: sort.direction } : null,
        page,
      }),
    enabled: Boolean(collection),
  });

  // A rejected filter (phase-41 guardrails) is the user's to fix, so say so plainly.
  useEffect(() => {
    if (docsQuery.error) {
      toast({
        title: 'Query rejected',
        description: errorMessage(docsQuery.error, 'Could not load documents'),
        variant: 'error',
      });
    }
  }, [docsQuery.error]);

  function refresh(): void {
    void queryClient.invalidateQueries({ queryKey: ['data-docs', projectId] });
    void queryClient.invalidateQueries({ queryKey: ['data-collections', projectId] });
  }

  const save = useMutation({
    mutationFn: (document: DataDocument) => {
      const id = editing?.doc?._id;
      return editing?.mode === 'edit' && id
        ? updateDocument(projectId, collection as string, String(id), document)
        : createDocument(projectId, collection as string, document);
    },
    onSuccess: () => {
      setEditing(null);
      setEditorError(null);
      refresh();
    },
    onError: (err) => setEditorError(errorMessage(err, 'Could not save the document')),
  });

  const remove = useMutation({
    mutationFn: (doc: DataDocument) =>
      deleteDocument(projectId, collection as string, String(doc._id)),
    onSuccess: () => {
      setPendingDelete(null);
      refresh();
      toast({ title: 'Document deleted', variant: 'success' });
    },
    onError: (err) => {
      setPendingDelete(null);
      toast({
        title: 'Delete failed',
        description: errorMessage(err, 'Could not delete the document'),
        variant: 'error',
      });
    },
  });

  const fields = useMemo(() => columnsOf(docsQuery.data?.documents ?? []), [docsQuery.data]);
  const info = infoQuery.data;

  function selectCollection(name: string): void {
    setCollection(name);
    setPage(1);
    setSort(null);
    setDraft(EMPTY_FILTER);
    setApplied(null);
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          Data
          {/* Scope, always visible — this is live application data. */}
          {info ? (
            <span data-testid="data-scope" className="flex items-center gap-1.5">
              <Badge tone={info.mode === 'byo' ? 'warning' : 'neutral'}>
                {info.mode === 'byo' ? 'your database' : 'project database'}
              </Badge>
              <code className="font-mono text-[11px] text-fg-subtle">{info.db_name}</code>
            </span>
          ) : null}
        </span>
        <Button
          size="sm"
          disabled={!collection}
          data-testid="doc-create"
          onClick={() => {
            setEditorError(null);
            setEditing({ mode: 'create', doc: null });
          }}
        >
          New document
        </Button>
      </header>

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-auto rounded-xl border border-edge bg-surface p-2">
          <h4 className="mb-1 px-2 text-[11px] uppercase tracking-wide text-fg-subtle">
            Collections
          </h4>
          <CollectionsList
            collections={collections}
            selected={collection}
            loading={collectionsQuery.isLoading}
            onSelect={selectCollection}
          />
        </aside>

        <section className="min-h-0 space-y-3 overflow-auto rounded-xl border border-edge bg-surface p-3">
          {collection ? (
            <>
              <FilterBar
                draft={draft}
                fields={fields}
                active={applied !== null}
                onChange={setDraft}
                onApply={() => {
                  setApplied(buildFilter(draft));
                  setPage(1);
                }}
                onClear={() => {
                  setDraft(EMPTY_FILTER);
                  setApplied(null);
                  setPage(1);
                }}
              />
              <DocTable
                page={docsQuery.data ?? null}
                loading={docsQuery.isFetching}
                sort={sort}
                onSortChange={(next) => {
                  setSort(next);
                  setPage(1);
                }}
                onPageChange={setPage}
                onView={(doc) => {
                  setEditorError(null);
                  setEditing({ mode: 'edit', doc });
                }}
                onDelete={setPendingDelete}
              />
            </>
          ) : (
            <p className="text-sm text-fg-subtle">Select a collection to browse its documents.</p>
          )}
        </section>
      </div>

      <DocEditor
        open={editing !== null}
        mode={editing?.mode ?? 'create'}
        collection={collection ?? ''}
        document={editing?.doc ?? null}
        saving={save.isPending}
        error={editorError}
        onSave={(doc) => save.mutate(doc)}
        onClose={() => {
          setEditing(null);
          setEditorError(null);
        }}
      />

      <DeleteConfirm
        open={pendingDelete !== null}
        collection={collection ?? ''}
        docId={String(pendingDelete?._id ?? '')}
        deleting={remove.isPending}
        onConfirm={() => pendingDelete && remove.mutate(pendingDelete)}
        onClose={() => setPendingDelete(null)}
      />
    </div>
  );
}

export default DataBrowser;
