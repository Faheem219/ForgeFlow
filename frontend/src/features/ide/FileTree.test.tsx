import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { FileNode } from '../../lib/types';
import { FileTree } from './FileTree';
import { buildTree } from './fileTreeModel';

const nodes: FileNode[] = [
  { path: 'src', type: 'dir', size: null },
  { path: 'src/App.tsx', type: 'file', size: 120 },
  { path: 'src/nested', type: 'dir', size: null },
  { path: 'src/nested/deep.ts', type: 'file', size: 10 },
  { path: 'package.json', type: 'file', size: 40 },
];

describe('buildTree', () => {
  it('nests files under their directories, directories first', () => {
    const tree = buildTree(nodes);
    expect(tree.map((n) => n.path)).toEqual(['src', 'package.json']);

    const src = tree[0];
    expect(src.type).toBe('dir');
    expect(src.children.map((c) => c.path)).toEqual(['src/nested', 'src/App.tsx']);
    expect(src.children[0].children.map((c) => c.path)).toEqual(['src/nested/deep.ts']);
  });

  it('synthesises intermediate directories the API did not report', () => {
    const tree = buildTree([{ path: 'a/b/c.ts', type: 'file', size: 1 }]);
    expect(tree.map((n) => n.path)).toEqual(['a']);
    expect(tree[0].children[0].path).toBe('a/b');
    expect(tree[0].children[0].children[0].path).toBe('a/b/c.ts');
  });

  it('returns an empty tree for no files', () => {
    expect(buildTree([])).toEqual([]);
  });
});

describe('FileTree', () => {
  it('renders root entries and opens a file on click', () => {
    const onOpen = vi.fn();
    render(<FileTree nodes={nodes} activePath={null} onOpen={onOpen} />);

    expect(screen.getByTestId('file-tree')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('tree-file-package.json'));
    expect(onOpen).toHaveBeenCalledWith('package.json');
  });

  it('expands a directory to reveal its children', () => {
    const onOpen = vi.fn();
    render(<FileTree nodes={nodes} activePath={null} onOpen={onOpen} />);

    // Collapsed by default: children are not rendered.
    expect(screen.queryByTestId('tree-file-src/App.tsx')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('tree-dir-src'));
    expect(screen.getByTestId('tree-file-src/App.tsx')).toBeInTheDocument();

    // Clicking a directory expands rather than opening a tab.
    expect(onOpen).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('tree-file-src/App.tsx'));
    expect(onOpen).toHaveBeenCalledWith('src/App.tsx');
  });

  it('collapses an expanded directory again', () => {
    render(<FileTree nodes={nodes} activePath={null} onOpen={vi.fn()} />);
    fireEvent.click(screen.getByTestId('tree-dir-src'));
    expect(screen.getByTestId('tree-file-src/App.tsx')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('tree-dir-src'));
    expect(screen.queryByTestId('tree-file-src/App.tsx')).not.toBeInTheDocument();
  });

  it('shows an empty message when the workspace has no files', () => {
    render(<FileTree nodes={[]} activePath={null} onOpen={vi.fn()} />);
    expect(screen.getByText('No files yet.')).toBeInTheDocument();
  });
});
