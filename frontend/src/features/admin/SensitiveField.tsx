import { useState } from 'react';

import { Badge } from '../../components/ui';

/**
 * A masked, write-only field for a secret (§7 secret handling).
 *
 * The current value is NEVER rendered — the backend only ever returns a mask (`••••••`) or an empty
 * string, and this component treats even that as opaque: it shows "set" / "not set", never the
 * characters. Editing is a fresh password input; the typed value only leaves the component via
 * `onSubmit`, and is not persisted in the DOM after.
 *
 * A secret submitted here is encrypted before it is stored and can never be read back — so the
 * field is deliberately asymmetric: you may replace a value or clear the override, never inspect
 * it. `readOnly` drops the editor entirely, for a key that cannot be set from the panel at all.
 */
export function SensitiveField({
  isSet,
  readOnly = false,
  busy = false,
  note,
  onSubmit,
}: {
  /** Whether a secret is currently stored (derived from the mask, never the value itself). */
  isSet: boolean;
  readOnly?: boolean;
  busy?: boolean;
  note?: string;
  onSubmit?: (secret: string) => void;
}): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');

  const status = (
    <span className="flex items-center gap-2">
      <span data-testid="sensitive-status" className="font-mono text-sm text-fg-muted">
        {isSet ? '••••••' : 'not set'}
      </span>
      <Badge tone={isSet ? 'success' : 'neutral'}>{isSet ? 'stored' : 'unset'}</Badge>
    </span>
  );

  if (readOnly) {
    return (
      <div className="space-y-1" data-testid="sensitive-field">
        {status}
        {note ? <p className="text-xs text-fg-subtle">{note}</p> : null}
      </div>
    );
  }

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center gap-2" data-testid="sensitive-field">
        {status}
        <button
          type="button"
          data-testid="sensitive-edit"
          disabled={busy}
          onClick={() => setEditing(true)}
          className="rounded px-2 py-1 text-xs text-brand-text hover:bg-surface-raised disabled:opacity-50"
        >
          {busy ? 'Saving…' : isSet ? 'Replace' : 'Set'}
        </button>
        {note ? <span className="text-xs text-fg-subtle">{note}</span> : null}
      </div>
    );
  }

  return (
    <form
      className="flex items-center gap-2"
      data-testid="sensitive-field"
      onSubmit={(e) => {
        e.preventDefault();
        if (draft) onSubmit?.(draft);
        setDraft('');
        setEditing(false);
      }}
    >
      <input
        type="password"
        autoComplete="new-password"
        aria-label="New secret value"
        data-testid="sensitive-input"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder="enter new value"
        className="w-48 rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1 text-sm text-fg"
      />
      <button
        type="submit"
        data-testid="sensitive-save"
        disabled={!draft}
        className="rounded bg-brand px-2 py-1 text-xs text-white disabled:opacity-50"
      >
        Save
      </button>
      <button
        type="button"
        onClick={() => {
          setDraft('');
          setEditing(false);
        }}
        className="rounded px-2 py-1 text-xs text-fg-muted hover:bg-surface-raised"
      >
        Cancel
      </button>
    </form>
  );
}
