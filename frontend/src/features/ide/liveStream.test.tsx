import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useRealtimeStore } from '../../lib/stores/realtimeStore';
import type { FileContent, FileNode } from '../../lib/types';
import type { RealtimeEvent } from '../../lib/wsClient';
import { Ide } from './Ide';
import { useIdeStore } from './ideStore';
import { UNLOCK_QUIET_MS, applyServerWrite, clearUnlockTimers } from './liveWrites';

// `vi.hoisted` runs with the (hoisted) vi.mock factories, so the mock fns exist before ./Ide's
// import pulls ./api in.
const api = vi.hoisted(() => ({
  getTree: vi.fn<(projectId: string, path?: string) => Promise<FileNode[]>>(),
  readFile: vi.fn<(projectId: string, path: string) => Promise<FileContent>>(),
  writeFile: vi.fn<(projectId: string, path: string, content: string) => Promise<FileNode>>(),
}));
vi.mock('./api', () => api);

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

const { getTree, readFile } = api;
let serverContent = 'export const generated = 1;\n';

function wrap(node: ReactNode): JSX.Element {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

/** Mount and let the initial tree query settle, so later assertions aren't racing it. */
async function renderIde(): Promise<void> {
  render(wrap(<Ide projectId="p1" />));
  await waitFor(() => expect(getTree).toHaveBeenCalled());
  await act(async () => {
    await Promise.resolve();
  });
}

function fsWrite(path: string, seq: number): RealtimeEvent {
  return {
    event: 'fs.write',
    project_id: 'p1',
    stage: 'build',
    payload: { path },
    seq,
    ts: '',
  };
}

/**
 * Push an event onto the realtime store exactly as the WS client would, then flush the
 * effect's async chain (fs.write → re-read → store update) inside act.
 */
async function emit(event: RealtimeEvent): Promise<void> {
  await act(async () => {
    useRealtimeStore.setState((s) => ({ events: [...s.events, event] }));
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  serverContent = 'export const generated = 1;\n';
  useIdeStore.getState().reset();
  useRealtimeStore.setState({ events: [] });
  vi.clearAllMocks();
  clearUnlockTimers();
  getTree.mockResolvedValue([]);
  // Reads always reflect whatever the "server" currently holds.
  readFile.mockImplementation((_projectId, path) =>
    Promise.resolve({ path, content: serverContent, size: serverContent.length }),
  );
});

afterEach(() => {
  clearUnlockTimers();
  useIdeStore.getState().reset();
});

describe('live generation streaming', () => {
  it('streams a server-written file into a focused read-only tab, then unlocks', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      await renderIde();
      await emit(fsWrite('src/generated.ts', 1));

      // Appears, focused, showing server content, read-only while generating.
      await waitFor(() => expect(screen.getByTestId('monaco')).toHaveValue(serverContent));
      expect(screen.getByTestId('monaco')).toHaveAttribute('readonly');
      expect(screen.getByTestId('generating-banner')).toBeInTheDocument();
      expect(screen.getByTestId('tab-generating-src/generated.ts')).toBeInTheDocument();

      // Once writes stop, the file hands back to the user.
      await act(async () => {
        vi.advanceTimersByTime(UNLOCK_QUIET_MS + 50);
      });
      await waitFor(() =>
        expect(screen.queryByTestId('generating-banner')).not.toBeInTheDocument(),
      );
      expect(screen.getByTestId('monaco')).not.toHaveAttribute('readonly');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps streaming updated content across successive writes', async () => {
    await renderIde();

    await emit(fsWrite('src/generated.ts', 1));
    await waitFor(() => expect(screen.getByTestId('monaco')).toHaveValue(serverContent));

    serverContent = 'export const generated = 2;\n// more\n';
    await emit(fsWrite('src/generated.ts', 2));
    await waitFor(() => expect(screen.getByTestId('monaco')).toHaveValue(serverContent));
    expect(readFile).toHaveBeenCalledTimes(2);
  });

  it('ignores events for other projects', async () => {
    await renderIde();
    await emit({ ...fsWrite('src/other.ts', 1), project_id: 'p2' });

    await new Promise((r) => setTimeout(r, 20));
    expect(readFile).not.toHaveBeenCalled();
  });

  it('does not replay writes buffered before the IDE mounted', async () => {
    // The workspace connects (and buffers events) before the Build stage is opened; replaying
    // that backlog would spray open a tab per past write.
    useRealtimeStore.setState({
      events: [fsWrite('src/old-a.ts', 1), fsWrite('src/old-b.ts', 2)],
    });

    await renderIde();
    await new Promise((r) => setTimeout(r, 20));

    expect(readFile).not.toHaveBeenCalled();
    expect(useIdeStore.getState().order).toEqual([]);

    // ...but a write arriving after mount is still picked up.
    await emit(fsWrite('src/new.ts', 3));
    await waitFor(() => expect(readFile).toHaveBeenCalledWith('p1', 'src/new.ts'));
  });

  it('suppresses the echo of our own save', async () => {
    useIdeStore.getState().openFile('src/mine.ts', 'mine');
    useIdeStore.getState().noteSelfWrite('src/mine.ts');

    await applyServerWrite('p1', 'src/mine.ts');

    // No re-read, and the file is not flipped into generating mode.
    expect(readFile).not.toHaveBeenCalled();
    expect(useIdeStore.getState().files['src/mine.ts'].generating).toBe(false);
  });

  it('raises a non-destructive conflict when a server write lands on unsaved edits', async () => {
    await renderIde();

    // Open a file and dirty it.
    act(() => {
      useIdeStore.getState().openFile('src/App.tsx', 'original');
      useIdeStore.getState().edit('src/App.tsx', 'my local edit');
    });

    serverContent = 'agent wrote this';
    await emit(fsWrite('src/App.tsx', 5));

    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());
    // Crucially: local edits are untouched and nothing was re-read over them.
    expect(useIdeStore.getState().files['src/App.tsx'].content).toBe('my local edit');
    expect(readFile).not.toHaveBeenCalled();
  });

  it('reloads from the server when the user resolves a conflict that way', async () => {
    await renderIde();
    act(() => {
      useIdeStore.getState().openFile('src/App.tsx', 'original');
      useIdeStore.getState().edit('src/App.tsx', 'my local edit');
    });

    serverContent = 'agent wrote this';
    await emit(fsWrite('src/App.tsx', 5));
    await waitFor(() => expect(screen.getByRole('dialog')).toBeInTheDocument());

    act(() => {
      screen.getByTestId('conflict-reload').click();
    });

    await waitFor(() =>
      expect(useIdeStore.getState().files['src/App.tsx'].content).toBe('agent wrote this'),
    );
    // Reloaded content is clean, and the prompt is gone.
    expect(useIdeStore.getState().files['src/App.tsx'].saved).toBe('agent wrote this');
    expect(useIdeStore.getState().conflict).toBeNull();
  });

  it('reloads a clean open file in place without prompting', async () => {
    await renderIde();
    act(() => {
      useIdeStore.getState().openFile('src/App.tsx', 'original');
    });

    serverContent = 'server update';
    await emit(fsWrite('src/App.tsx', 3));

    await waitFor(() => expect(screen.getByTestId('monaco')).toHaveValue('server update'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
