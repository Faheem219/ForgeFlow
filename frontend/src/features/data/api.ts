import { apiFetch } from '../../lib/apiClient';
import type { CollectionDto, DataDocument, DbInfoDto, DocumentPageDto } from '../../lib/types';

/** Where this project's data lives — used for the always-visible scope banner. */
export function getDbInfo(projectId: string): Promise<DbInfoDto> {
  return apiFetch<DbInfoDto>(`/projects/${projectId}/data/info`);
}

export function listCollections(projectId: string): Promise<CollectionDto[]> {
  return apiFetch<CollectionDto[]>(`/projects/${projectId}/data/collections`);
}

export interface ListDocumentsParams {
  filter?: Record<string, unknown> | null;
  sort?: Record<string, 1 | -1> | null;
  page?: number;
  limit?: number;
}

export function listDocuments(
  projectId: string,
  collection: string,
  { filter, sort, page = 1, limit }: ListDocumentsParams = {},
): Promise<DocumentPageDto> {
  const params = new URLSearchParams({ page: String(page) });
  if (limit) params.set('limit', String(limit));
  // Filters and sorts travel as JSON; the API validates them against its operator allowlist.
  if (filter && Object.keys(filter).length > 0) params.set('filter', JSON.stringify(filter));
  if (sort && Object.keys(sort).length > 0) params.set('sort', JSON.stringify(sort));

  return apiFetch<DocumentPageDto>(
    `/projects/${projectId}/data/collections/${encodeURIComponent(collection)}/docs?${params}`,
  );
}

export function createDocument(
  projectId: string,
  collection: string,
  document: DataDocument,
): Promise<DataDocument> {
  return apiFetch<DataDocument>(
    `/projects/${projectId}/data/collections/${encodeURIComponent(collection)}/docs`,
    { method: 'POST', body: JSON.stringify({ document }) },
  );
}

export function updateDocument(
  projectId: string,
  collection: string,
  docId: string,
  document: DataDocument,
): Promise<DataDocument> {
  return apiFetch<DataDocument>(
    `/projects/${projectId}/data/collections/${encodeURIComponent(collection)}/docs/${encodeURIComponent(docId)}`,
    { method: 'PUT', body: JSON.stringify({ document }) },
  );
}

/** Deletes are immediate and irreversible — the UI gates this behind an explicit confirmation. */
export function deleteDocument(
  projectId: string,
  collection: string,
  docId: string,
): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(
    `/projects/${projectId}/data/collections/${encodeURIComponent(collection)}/docs/${encodeURIComponent(docId)}`,
    { method: 'DELETE' },
  );
}
