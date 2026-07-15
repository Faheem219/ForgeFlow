// The FS API returns a flat list of workspace-relative paths; the tree UI needs a nested shape.
// Kept pure and separate from the component so the shaping rules are directly testable.

import type { FileNode } from '../../lib/types';

export interface TreeNode {
  name: string;
  path: string;
  type: 'file' | 'dir';
  children: TreeNode[];
}

function sortNodes(nodes: TreeNode[]): TreeNode[] {
  nodes.sort((a, b) => {
    if (a.type !== b.type) return a.type === 'dir' ? -1 : 1; // directories first
    return a.name.localeCompare(b.name);
  });
  nodes.forEach((n) => sortNodes(n.children));
  return nodes;
}

/**
 * Build a nested tree from flat FS nodes. Intermediate directories are synthesised when the API
 * only reports leaves, so a tree never silently drops a nested file.
 */
export function buildTree(nodes: FileNode[]): TreeNode[] {
  const roots: TreeNode[] = [];
  const byPath = new Map<string, TreeNode>();

  const ensureDir = (path: string): TreeNode | null => {
    if (!path) return null;
    const existing = byPath.get(path);
    if (existing) return existing;
    const segments = path.split('/');
    const node: TreeNode = {
      name: segments[segments.length - 1],
      path,
      type: 'dir',
      children: [],
    };
    byPath.set(path, node);
    const parent = ensureDir(segments.slice(0, -1).join('/'));
    (parent ? parent.children : roots).push(node);
    return node;
  };

  // Directories first so a file never creates a dir placeholder that later conflicts.
  for (const node of nodes.filter((n) => n.type === 'dir')) {
    ensureDir(node.path);
  }
  for (const node of nodes.filter((n) => n.type === 'file')) {
    const segments = node.path.split('/');
    const leaf: TreeNode = {
      name: segments[segments.length - 1],
      path: node.path,
      type: 'file',
      children: [],
    };
    byPath.set(node.path, leaf);
    const parent = ensureDir(segments.slice(0, -1).join('/'));
    (parent ? parent.children : roots).push(leaf);
  }

  return sortNodes(roots);
}

/** Every directory path in the tree (used to expand ancestors when a file streams in). */
export function ancestorDirs(path: string): string[] {
  const segments = path.split('/').slice(0, -1);
  return segments.map((_, i) => segments.slice(0, i + 1).join('/'));
}
