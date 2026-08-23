import { useState } from 'react';

import { Button, Modal } from '../../components/ui';
import type { DataDocument } from '../../lib/types';

/**
 * View/edit a document as validated JSON.
 *
 * JSON keeps the browser schema-agnostic — generated apps vary, and ForgeFlow has no model of their
 * documents — while still refusing malformed writes before they reach the API. `_id` is shown but
 * the server ignores it on update, so a document can be round-tripped whole.
 */
export function DocEditor({
  open,
  mode,
  collection,
  document: initial,
  saving = false,
  error,
  onSave,
  onClose,
}: {
  open: boolean;
  mode: 'create' | 'edit';
  collection: string;
  document?: DataDocument | null;
  saving?: boolean;
  error?: string | null;
  onSave: (document: DataDocument) => void;
  onClose: () => void;
}): JSX.Element | null {
  const [text, setText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [seeded, setSeeded] = useState(false);

  // Seed the editor once per opening, so typing is never clobbered by a re-render.
  if (open && !seeded) {
    setText(initial ? JSON.stringify(initial, null, 2) : '{\n  \n}');
    setJsonError(null);
    setSeeded(true);
  }
  if (!open && seeded) setSeeded(false);

  function submit(): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      setJsonError(err instanceof Error ? err.message : 'Invalid JSON');
      return;
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      setJsonError('A document must be a JSON object');
      return;
    }
    setJsonError(null);
    onSave(parsed as DataDocument);
  }

  if (!open) return null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`${mode === 'create' ? 'New document' : 'Edit document'} · ${collection}`}
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} data-testid="editor-cancel">
            Cancel
          </Button>
          <Button size="sm" onClick={submit} loading={saving} data-testid="editor-save">
            {mode === 'create' ? 'Create' : 'Save'}
          </Button>
        </>
      }
    >
      <textarea
        aria-label="Document JSON"
        data-testid="editor-json"
        rows={14}
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        className="w-full rounded-lg border border-edge-strong bg-surface-sunken p-2 font-mono text-xs text-fg outline-none focus:border-brand"
      />
      {jsonError ? (
        <p className="mt-2 text-xs text-danger" data-testid="editor-json-error">
          {jsonError}
        </p>
      ) : null}
      {/* Guardrail rejections from the API (phase-41) land here rather than in a toast that
          disappears while the user is still looking at their document. */}
      {error ? (
        <p className="mt-2 text-xs text-danger" data-testid="editor-api-error">
          {error}
        </p>
      ) : null}
    </Modal>
  );
}

/** Deletes hit real application data and there are no backups — so this is explicit, not a toast. */
export function DeleteConfirm({
  open,
  collection,
  docId,
  deleting = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  collection: string;
  docId: string;
  deleting?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}): JSX.Element | null {
  if (!open) return null;
  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Delete this document?"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} data-testid="delete-cancel">
            Cancel
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={onConfirm}
            loading={deleting}
            data-testid="delete-confirm"
          >
            Delete
          </Button>
        </>
      }
    >
      <p data-testid="delete-warning">
        This permanently removes <code className="font-mono text-fg">{docId}</code> from{' '}
        <code className="font-mono text-fg">{collection}</code>. It affects your live app data and
        cannot be undone.
      </p>
    </Modal>
  );
}

export default DocEditor;
