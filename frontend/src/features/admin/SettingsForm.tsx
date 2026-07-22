import { useMutation } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { Badge, Button } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { putConfig, resetConfig } from './api';
import { SensitiveField } from './SensitiveField';
import { SourceBadge } from './SourceBadge';
import type { SettingView } from './types';

/** Turn an editor draft back into the value the API expects; the backend re-validates. */
function toValue(setting: SettingView, draft: string, boolDraft: boolean): unknown {
  if (setting.type === 'bool') return boolDraft;
  if (setting.type === 'int') return draft === '' ? null : Number(draft);
  if (setting.type === 'float') return draft === '' ? null : Number(draft);
  return draft;
}

function Editor({
  setting,
  draft,
  setDraft,
  boolDraft,
  setBoolDraft,
  disabled,
}: {
  setting: SettingView;
  draft: string;
  setDraft: (v: string) => void;
  boolDraft: boolean;
  setBoolDraft: (v: boolean) => void;
  disabled: boolean;
}): JSX.Element {
  const cls =
    'rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100 disabled:opacity-50';

  if (setting.type === 'bool') {
    return (
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          data-testid={`toggle-${setting.key}`}
          checked={boolDraft}
          disabled={disabled}
          onChange={(e) => setBoolDraft(e.target.checked)}
        />
        {boolDraft ? 'on' : 'off'}
      </label>
    );
  }
  if (setting.type === 'enum') {
    return (
      <select
        data-testid={`input-${setting.key}`}
        value={draft}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        className={cls}
      >
        {setting.choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
    );
  }
  if (setting.type === 'json') {
    return (
      <textarea
        data-testid={`input-${setting.key}`}
        value={draft}
        rows={3}
        disabled={disabled}
        onChange={(e) => setDraft(e.target.value)}
        className={`${cls} w-72 font-mono text-xs`}
      />
    );
  }
  return (
    <input
      type={setting.type === 'int' || setting.type === 'float' ? 'number' : 'text'}
      data-testid={`input-${setting.key}`}
      value={draft}
      disabled={disabled}
      onChange={(e) => setDraft(e.target.value)}
      className={`${cls} w-72`}
    />
  );
}

function SettingRow({
  setting,
  onChanged,
}: {
  setting: SettingView;
  onChanged: () => void;
}): JSX.Element {
  const initial = setting.type === 'bool' ? '' : String(setting.value ?? '');
  const [draft, setDraft] = useState(initial);
  const [boolDraft, setBoolDraft] = useState(Boolean(setting.value));
  const [error, setError] = useState<string | null>(null);

  // Follow the server value after a save/reset re-fetches the catalog.
  useEffect(() => {
    setDraft(setting.type === 'bool' ? '' : String(setting.value ?? ''));
    setBoolDraft(Boolean(setting.value));
    setError(null);
  }, [setting.value, setting.type]);

  const save = useMutation({
    mutationFn: () => putConfig(setting.key, toValue(setting, draft, boolDraft)),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Save failed'),
  });

  const reset = useMutation({
    mutationFn: () => resetConfig(setting.key),
    onSuccess: () => {
      setError(null);
      onChanged();
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Reset failed'),
  });

  const busy = save.isPending || reset.isPending;
  const dirty =
    setting.type === 'bool'
      ? boolDraft !== Boolean(setting.value)
      : draft !== String(setting.value ?? '');
  // Only a `db` override can be reset back to env/default.
  const canReset = setting.source === 'db';

  return (
    <div
      className="flex flex-col gap-2 rounded-xl border border-slate-800 p-3"
      data-testid={`setting-${setting.key}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <code className="text-sm font-medium text-slate-100">{setting.key}</code>
            <SourceBadge source={setting.source} />
            {setting.restart_required ? (
              <span title="Applies to newly-created resources; existing ones need a restart/recreate">
                <Badge tone="warning">restart required</Badge>
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 text-xs text-slate-500">{setting.description}</p>
        </div>
      </div>

      {setting.sensitive ? (
        <SensitiveField
          isSet={setting.value === '••••••'}
          readOnly
          note="Secrets are managed in the encrypted credentials vault, not platform config."
        />
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Editor
            setting={setting}
            draft={draft}
            setDraft={setDraft}
            boolDraft={boolDraft}
            setBoolDraft={setBoolDraft}
            disabled={busy}
          />
          <Button
            size="sm"
            disabled={busy || !dirty}
            data-testid={`save-${setting.key}`}
            onClick={() => save.mutate()}
          >
            {save.isPending ? 'Saving…' : 'Save'}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || !canReset}
            title={canReset ? 'Revert to env/default' : 'Not overridden'}
            data-testid={`reset-${setting.key}`}
            onClick={() => reset.mutate()}
          >
            Reset
          </Button>
          <span className="text-xs text-slate-600">
            default: <span className="font-mono">{String(setting.default ?? '—') || '—'}</span>
          </span>
        </div>
      )}

      {error ? (
        <p className="text-xs text-red-400" data-testid={`error-${setting.key}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function SettingsForm({
  settings,
  onChanged,
}: {
  settings: SettingView[];
  onChanged: () => void;
}): JSX.Element {
  if (settings.length === 0) {
    return <p className="text-sm text-slate-500">No settings in this section.</p>;
  }
  return (
    <div className="space-y-2" data-testid="settings-form">
      {settings.map((s) => (
        <SettingRow key={s.key} setting={s} onChanged={onChanged} />
      ))}
    </div>
  );
}

export default SettingsForm;
