import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';

import { Button, EmptyState, Modal, ResizablePanel, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { PanelKey } from '../../lib/stores/uiStore';
import { toast } from '../../lib/stores/toastStore';
import { CodeEditor } from './CodeEditor';
import { EditorTabs } from './EditorTabs';
import { FileTree } from './FileTree';
import { getTree, readFile, writeFile } from './api';
import { isDirty, useIdeStore } from './ideStore';
import { useLiveWrites } from './liveWrites';

export function Ide({ projectId }: { projectId: string }): JSX.Element {
  const queryClient = useQueryClient();
  const [saving, setSaving] = useState(false);

  const files = useIdeStore((s) => s.files);
  const order = useIdeStore((s) => s.order);
  const active = useIdeStore((s) => s.active);
  const conflict = useIdeStore((s) => s.conflict);

  const treeQuery = useQuery({
    queryKey: ['fs-tree', projectId],
    queryFn: () => getTree(projectId),
    enabled: Boolean(projectId),
  });

  useLiveWrites(projectId);

  // A server write may introduce a new file — keep the tree in step.
  const activeFile = active ? files[active] : undefined;
  useEffect(() => {
    if (activeFile?.generating) {
      void queryClient.invalidateQueries({ queryKey: ['fs-tree', projectId] });
    }
  }, [activeFile?.generating, projectId, queryClient]);

  // Reset per project so tabs from a previous project never leak in.
  useEffect(() => {
    useIdeStore.getState().reset();
  }, [projectId]);

  const open = useCallback(
    async (path: string) => {
      const existing = useIdeStore.getState().files[path];
      if (existing) {
        useIdeStore.getState().setActive(path);
        return;
      }
      try {
        const file = await readFile(projectId, path);
        useIdeStore.getState().openFile(path, file.content);
      } catch (err) {
        const message = err instanceof ApiError ? err.message : 'Could not open file';
        toast({ title: 'Open failed', description: message, variant: 'error' });
      }
    },
    [projectId],
  );

  const save = useCallback(async () => {
    const state = useIdeStore.getState();
    const path = state.active;
    if (!path) return;
    const file = state.files[path];
    if (!file || file.generating || !isDirty(file)) return;

    const content = file.content;
    setSaving(true);
    // Mark before the request so the resulting fs.write echo isn't mistaken for an agent write.
    state.noteSelfWrite(path);
    try {
      await writeFile(projectId, path, content);
      useIdeStore.getState().markSaved(path, content);
      void queryClient.invalidateQueries({ queryKey: ['fs-tree', projectId] });
    } catch (err) {
      useIdeStore.getState().consumeSelfWrite(path); // the echo will never arrive
      const message = err instanceof ApiError ? err.message : 'Could not save file';
      toast({ title: 'Save failed', description: message, variant: 'error' });
    } finally {
      setSaving(false);
    }
  }, [projectId, queryClient]);

  // Ctrl/Cmd-S saves the focused file, as in a native editor.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [save]);

  const resolveConflict = useCallback(
    async (choice: 'mine' | 'reload') => {
      const path = useIdeStore.getState().conflict;
      if (!path) return;
      useIdeStore.getState().setConflict(null);
      if (choice === 'mine') return; // keep local edits; the next save wins
      try {
        const file = await readFile(projectId, path);
        useIdeStore.getState().openFile(path, file.content); // replaces content + clears dirty
      } catch {
        toast({
          title: 'Reload failed',
          description: 'Could not re-read the file.',
          variant: 'error',
        });
      }
    },
    [projectId],
  );

  return (
    <div className="panel flex h-full min-h-0 overflow-hidden">
      <ResizablePanel
        panelKey={PanelKey.ideTree}
        label="Resize file tree"
        min={160}
        max={480}
        className="overflow-hidden border-r border-subtle"
      >
        <div className="flex shrink-0 items-center justify-between px-3 py-2">
          <span className="eyebrow">Files</span>
          {treeQuery.isFetching ? <Spinner size="sm" className="text-faint" /> : null}
        </div>
        <div className="min-h-0 flex-1 overflow-auto pb-2">
          {treeQuery.isError ? (
            <p className="px-3 text-xs text-muted">
              Workspace unavailable — the sandbox may still be starting.
            </p>
          ) : (
            <FileTree
              nodes={treeQuery.data ?? []}
              activePath={active}
              onOpen={(path) => void open(path)}
            />
          )}
        </div>
      </ResizablePanel>

      <div className="flex min-w-0 flex-1 flex-col">
        <EditorTabs
          files={files}
          order={order}
          active={active}
          onSelect={(path) => useIdeStore.getState().setActive(path)}
          onClose={(path) => useIdeStore.getState().closeFile(path)}
        />

        {activeFile && active ? (
          <>
            <div className="flex shrink-0 items-center justify-between gap-2 border-b border-subtle px-3 py-1.5">
              <span className="truncate font-mono text-2xs text-faint">{active}</span>
              <div className="flex items-center gap-2">
                {activeFile.generating ? (
                  <span
                    className="flex items-center gap-1.5 text-2xs text-warning"
                    data-testid="generating-banner"
                  >
                    <span className="motion-essential h-1.5 w-1.5 animate-pulse-dot rounded-full bg-warning" />
                    generating… (read-only)
                  </span>
                ) : null}
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={saving || activeFile.generating || !isDirty(activeFile)}
                  onClick={() => void save()}
                  data-testid="save-button"
                >
                  {saving ? 'Saving…' : 'Save'}
                </Button>
              </div>
            </div>
            <div className="min-h-0 flex-1">
              <CodeEditor
                path={active}
                value={activeFile.content}
                readOnly={activeFile.generating}
                onChange={(value) => useIdeStore.getState().edit(active, value)}
              />
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState
              title="No file open"
              description="Pick a file from the tree — or watch one appear as the agent writes it."
            />
          </div>
        )}
      </div>

      <Modal
        open={conflict !== null}
        onClose={() => void resolveConflict('mine')}
        title="File changed on the server"
      >
        <div className="space-y-4">
          <p className="text-sm text-secondary">
            <span className="font-mono text-xs font-medium text-primary">{conflict}</span> was
            written in the sandbox while you had unsaved changes. Nothing has been overwritten.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => void resolveConflict('mine')}>
              Keep my changes
            </Button>
            <Button onClick={() => void resolveConflict('reload')} data-testid="conflict-reload">
              Discard mine &amp; reload
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

export default Ide;
