import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { FileContent, FileNode } from '../../lib/types';
import { Ide } from './Ide';
import { useIdeStore } from './ideStore';

// `vi.hoisted` runs with the (hoisted) vi.mock factories, so the mock fns exist before ./Ide's
// import pulls ./api in.
const api = vi.hoisted(() => ({
  getTree: vi.fn<(projectId: string, path?: string) => Promise<FileNode[]>>(),
  readFile: vi.fn<(projectId: string, path: string) => Promise<FileContent>>(),
  writeFile: vi.fn<(projectId: string, path: string, content: string) => Promise<FileNode>>(),
}));
vi.mock('./api', () => api);

// Monaco needs real layout/workers, which jsdom lacks — stand in a textarea so editing is real.
vi.mock('./monacoSetup', () => ({
  FORGEFLOW_DARK: 'forgeflow-dark',
  MONACO_THEMES: { dark: 'forgeflow-dark', light: 'forgeflow-light' },
}));
vi.mock('@monaco-editor/react', () => ({
  default: ({
    value,
    onChange,
    options,
  }: {
    value: string;
    onChange: (v: string | undefined) => void;
    options: { readOnly: boolean };
  }) => (
    <textarea
      data-testid="monaco"
      readOnly={options.readOnly}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
}));

const { getTree, readFile, writeFile } = api;
const tree: FileNode[] = [{ path: 'src/App.tsx', type: 'file', size: 5 }];

function wrap(node: ReactNode): JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

async function openAppFile(): Promise<void> {
  render(wrap(<Ide projectId="p1" />));
  fireEvent.click(await screen.findByTestId('tree-dir-src'));
  fireEvent.click(screen.getByTestId('tree-file-src/App.tsx'));
  await screen.findByTestId('monaco');
}

beforeEach(() => {
  useIdeStore.getState().reset();
  vi.clearAllMocks();
  getTree.mockResolvedValue(tree);
  readFile.mockImplementation((_projectId, path) =>
    Promise.resolve({ path, content: 'original', size: 8 }),
  );
  writeFile.mockResolvedValue({ path: 'src/App.tsx', type: 'file', size: 7 });
});

afterEach(() => {
  useIdeStore.getState().reset();
});

describe('IDE save', () => {
  it('opens a file into a tab with its server content', async () => {
    await openAppFile();

    expect(readFile).toHaveBeenCalledWith('p1', 'src/App.tsx');
    expect(screen.getByTestId('tab-src/App.tsx')).toBeInTheDocument();
    expect(screen.getByTestId('monaco')).toHaveValue('original');
  });

  it('marks the tab dirty on edit and clean again after save', async () => {
    await openAppFile();

    expect(screen.queryByTestId('tab-dirty-src/App.tsx')).not.toBeInTheDocument();
    expect(screen.getByTestId('save-button')).toBeDisabled(); // nothing to save yet

    fireEvent.change(screen.getByTestId('monaco'), { target: { value: 'edited!' } });
    expect(screen.getByTestId('tab-dirty-src/App.tsx')).toBeInTheDocument();
    expect(screen.getByTestId('save-button')).toBeEnabled();

    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => expect(writeFile).toHaveBeenCalledWith('p1', 'src/App.tsx', 'edited!'));
    await waitFor(() =>
      expect(screen.queryByTestId('tab-dirty-src/App.tsx')).not.toBeInTheDocument(),
    );
  });

  it('saves with Ctrl/Cmd-S', async () => {
    await openAppFile();
    fireEvent.change(screen.getByTestId('monaco'), { target: { value: 'via-keyboard' } });

    fireEvent.keyDown(window, { key: 's', metaKey: true });

    await waitFor(() =>
      expect(writeFile).toHaveBeenCalledWith('p1', 'src/App.tsx', 'via-keyboard'),
    );
  });

  it('does not write when there are no changes', async () => {
    await openAppFile();
    fireEvent.keyDown(window, { key: 's', ctrlKey: true });
    await waitFor(() => expect(writeFile).not.toHaveBeenCalled());
  });

  it('records a self-write so the save echo is not treated as an agent write', async () => {
    await openAppFile();
    fireEvent.change(screen.getByTestId('monaco'), { target: { value: 'mine' } });
    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    // The pending marker is what suppresses the incoming fs.write echo.
    expect(useIdeStore.getState().consumeSelfWrite('src/App.tsx')).toBe(true);
  });

  it('keeps the file dirty when the save fails', async () => {
    writeFile.mockRejectedValueOnce(new Error('boom'));
    await openAppFile();
    fireEvent.change(screen.getByTestId('monaco'), { target: { value: 'unsaved' } });
    fireEvent.click(screen.getByTestId('save-button'));

    await waitFor(() => expect(writeFile).toHaveBeenCalled());
    expect(screen.getByTestId('tab-dirty-src/App.tsx')).toBeInTheDocument();
  });

  it('closes a tab', async () => {
    await openAppFile();
    fireEvent.click(screen.getByTestId('tab-close-src/App.tsx'));
    expect(screen.queryByTestId('tab-src/App.tsx')).not.toBeInTheDocument();
  });
});
