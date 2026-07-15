import { apiFetch } from '../../lib/apiClient';
import type { FileContent, FileNode } from '../../lib/types';

export function getTree(projectId: string, path = '.'): Promise<FileNode[]> {
  return apiFetch<FileNode[]>(`/projects/${projectId}/fs/tree?path=${encodeURIComponent(path)}`);
}

export function readFile(projectId: string, path: string): Promise<FileContent> {
  return apiFetch<FileContent>(`/projects/${projectId}/fs/file?path=${encodeURIComponent(path)}`);
}

export function writeFile(projectId: string, path: string, content: string): Promise<FileNode> {
  return apiFetch<FileNode>(`/projects/${projectId}/fs/file`, {
    method: 'PUT',
    body: JSON.stringify({ path, content }),
  });
}
