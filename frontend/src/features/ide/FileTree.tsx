import { ChevronRight, FileCode2, Folder, FolderOpen } from 'lucide-react';
import { useMemo, useState } from 'react';

import { cn } from '../../lib/cn';
import type { FileNode } from '../../lib/types';
import { type TreeNode, buildTree } from './fileTreeModel';

interface FileTreeProps {
  nodes: FileNode[];
  activePath: string | null;
  onOpen: (path: string) => void;
}

function Row({
  node,
  depth,
  expanded,
  activePath,
  onToggle,
  onOpen,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  activePath: string | null;
  onToggle: (path: string) => void;
  onOpen: (path: string) => void;
}): JSX.Element {
  const isDir = node.type === 'dir';
  const isOpen = expanded.has(node.path);
  const isActive = node.path === activePath;

  return (
    <li>
      <button
        type="button"
        data-testid={`tree-${node.type}-${node.path}`}
        onClick={() => (isDir ? onToggle(node.path) : onOpen(node.path))}
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
        className={`group flex w-full items-center gap-1 rounded py-1 pr-2 text-left text-sm transition-colors ${
          isActive ? 'bg-brand/15 text-brand-text' : 'text-fg-muted hover:bg-surface-raised'
        }`}
      >
        <span
          className="flex w-3.5 shrink-0 items-center justify-center text-fg-subtle"
          aria-hidden
        >
          {isDir ? (
            <ChevronRight
              className={cn('h-3 w-3 transition-transform duration-150', isOpen && 'rotate-90')}
              strokeWidth={2}
            />
          ) : null}
        </span>
        <span
          aria-hidden
          className={cn('shrink-0', isActive ? 'text-brand-text' : 'text-fg-subtle')}
        >
          {isDir ? (
            isOpen ? (
              <FolderOpen className="h-3.5 w-3.5" strokeWidth={1.75} />
            ) : (
              <Folder className="h-3.5 w-3.5" strokeWidth={1.75} />
            )
          ) : (
            <FileCode2 className="h-3.5 w-3.5" strokeWidth={1.75} />
          )}
        </span>
        <span className="truncate">{node.name}</span>
      </button>
      {isDir && isOpen ? (
        <ul>
          {node.children.map((child) => (
            <Row
              key={child.path}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              activePath={activePath}
              onToggle={onToggle}
              onOpen={onOpen}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function FileTree({ nodes, activePath, onOpen }: FileTreeProps): JSX.Element {
  const tree = useMemo(() => buildTree(nodes), [nodes]);
  // Top-level directories start expanded; the workspace root is shallow enough that this is
  // the useful default rather than a wall of collapsed folders.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const toggle = (path: string): void =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  if (tree.length === 0) {
    return <p className="px-2 py-1 text-sm text-fg-subtle">No files yet.</p>;
  }

  return (
    <ul data-testid="file-tree">
      {tree.map((node) => (
        <Row
          key={node.path}
          node={node}
          depth={0}
          expanded={expanded}
          activePath={activePath}
          onToggle={toggle}
          onOpen={onOpen}
        />
      ))}
    </ul>
  );
}
