import { apiFetch } from '../../lib/apiClient';
import type { FileContent, FileNode, PreviewInfo } from '../../lib/types';

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

// --- live preview (phase-15) ---

export function getPreview(projectId: string): Promise<PreviewInfo> {
  return apiFetch<PreviewInfo>(`/projects/${projectId}/preview/status`);
}

export function previewAction(
  projectId: string,
  action: 'start' | 'stop' | 'restart',
): Promise<PreviewInfo> {
  return apiFetch<PreviewInfo>(`/projects/${projectId}/preview/${action}`, { method: 'POST' });
}
