import { ChevronRight } from 'lucide-react';
import { useMemo, useState } from 'react';

import { cn } from '../../lib/cn';
import type { FileNode } from '../../lib/types';
import { fileIcon } from './fileIcons';
import { type TreeNode, buildTree } from './fileTreeModel';

interface FileTreeProps {
  nodes: FileNode[];
  activePath: string | null;
  onOpen: (path: string) => void;
}

/** Horizontal step per nesting level — also where the indent guide for that level is drawn. */
const INDENT = 12;
const BASE = 8;

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
  const { icon: FileGlyph, tone } = fileIcon(node.path);

  return (
    <li className="relative">
      <button
        type="button"
        data-testid={`tree-${node.type}-${node.path}`}
        aria-expanded={isDir ? isOpen : undefined}
        aria-current={isActive ? 'true' : undefined}
        onClick={() => (isDir ? onToggle(node.path) : onOpen(node.path))}
        style={{ paddingLeft: `${depth * INDENT + BASE}px` }}
        title={node.path}
        className={cn(
          'group flex h-[26px] w-full items-center gap-1.5 pr-2 text-left text-[13px] transition-colors duration-100',
          isActive ? 'bg-brand/[0.14] text-fg' : 'text-fg-muted hover:bg-fg/[0.04] hover:text-fg',
        )}
      >
        <span className="flex w-3 shrink-0 items-center justify-center text-fg-faint" aria-hidden>
          {isDir ? (
            <ChevronRight
              className={cn('h-3 w-3 transition-transform duration-150', isOpen && 'rotate-90')}
              strokeWidth={2.25}
            />
          ) : null}
        </span>
        {isDir ? null : (
          <FileGlyph aria-hidden className={cn('h-3.5 w-3.5 shrink-0', tone)} strokeWidth={1.75} />
        )}
        <span className={cn('truncate', isDir && 'font-medium text-fg-muted group-hover:text-fg')}>
          {node.name}
        </span>
      </button>
      {isDir && isOpen ? (
        <ul className="relative">
          {/* Indent guide for this folder's children. */}
          <span
            aria-hidden
            className="pointer-events-none absolute bottom-0 top-0 w-px bg-edge/70"
            style={{ left: `${depth * INDENT + BASE + 5.5}px` }}
          />
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
    return <p className="px-3 py-1 text-xs text-fg-faint">No files yet.</p>;
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
