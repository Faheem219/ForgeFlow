import { useMutation } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { Badge, Button } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { putConfig, putConfigBulk, resetConfig } from './api';
import { SensitiveField } from './SensitiveField';
import { SourceBadge } from './SourceBadge';
import type { SettingView } from './types';

type Draft = string | boolean;

/** The editor state that matches what the server currently reports. */
function serverDraft(setting: SettingView): Draft {
  return setting.type === 'bool' ? Boolean(setting.value) : String(setting.value ?? '');
}

/** Turn an editor draft back into the value the API expects; the backend re-validates. */
function toValue(setting: SettingView, draft: Draft): unknown {
  if (setting.type === 'bool') return Boolean(draft);
  if (setting.type === 'int' || setting.type === 'float') {
    return draft === '' ? null : Number(draft);
  }
  return String(draft);
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** "1 – 20", "≥ 0.1", "≤ 16" — the bounds the backend will enforce, stated up front. */
function rangeHint(setting: SettingView): string | null {
  const { minimum, maximum } = setting;
  if (minimum != null && maximum != null) return `${minimum} – ${maximum}`;
  if (minimum != null) return `≥ ${minimum}`;
  if (maximum != null) return `≤ ${maximum}`;
  return null;
}

function displayValue(setting: SettingView): string {
  const value = setting.value;
  if (value === '' || value == null) return '—';
  return String(value);
}

const FIELD =
  'rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-100 ' +
  'outline-none focus:border-brand-500 disabled:opacity-50';

function Editor({
  setting,
  draft,
  onDraft,
  disabled,
}: {
  setting: SettingView;
  draft: Draft;
  onDraft: (v: Draft) => void;
  disabled: boolean;
}): JSX.Element {
  if (setting.type === 'bool') {
    return (
      <label className="flex items-center gap-2 text-sm text-slate-300">
        <input
          type="checkbox"
          data-testid={`toggle-${setting.key}`}
          className="h-4 w-4 accent-brand-500"
          checked={Boolean(draft)}
          disabled={disabled}
          onChange={(e) => onDraft(e.target.checked)}
        />
        {draft ? 'on' : 'off'}
      </label>
    );
  }
  if (setting.type === 'enum') {
    return (
      <select
        data-testid={`input-${setting.key}`}
        value={String(draft)}
        disabled={disabled}
        onChange={(e) => onDraft(e.target.value)}
        className={`${FIELD} w-72`}
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
        value={String(draft)}
        rows={3}
        disabled={disabled}
        onChange={(e) => onDraft(e.target.value)}
        className={`${FIELD} w-full max-w-xl font-mono text-xs`}
      />
    );
  }
  const numeric = setting.type === 'int' || setting.type === 'float';
  return (
    <input
      type={numeric ? 'number' : 'text'}
      data-testid={`input-${setting.key}`}
      value={String(draft)}
      disabled={disabled}
      min={setting.minimum ?? undefined}
      max={setting.maximum ?? undefined}
      step={setting.type === 'float' ? 'any' : undefined}
      placeholder={setting.nullable ? 'blank = unset' : undefined}
      onChange={(e) => onDraft(e.target.value)}
      className={`${FIELD} ${numeric ? 'w-40' : 'w-full max-w-xl'}`}
    />
  );
}

function SettingRow({
  setting,
  draft,
  onDraft,
  error,
  onError,
  onSaved,
  showCategory,
}: {
  setting: SettingView;
  draft: Draft | undefined;
  onDraft: (v: Draft | undefined) => void;
  error?: string;
  onError: (message: string | undefined) => void;
  onSaved: () => void;
  showCategory?: string;
}): JSX.Element {
  const current = draft ?? serverDraft(setting);
  const dirty = draft !== undefined && draft !== serverDraft(setting);
  // Only a `db` override can be reset back to env/default.
  const canReset = setting.source === 'db';

  const save = useMutation({
    mutationFn: () => putConfig(setting.key, toValue(setting, current)),
    onSuccess: () => {
      onError(undefined);
      onDraft(undefined);
      onSaved();
    },
    onError: (err) => onError(errorMessage(err, 'Save failed')),
  });

  const reset = useMutation({
    mutationFn: () => resetConfig(setting.key),
    onSuccess: () => {
      onError(undefined);
      onDraft(undefined);
      onSaved();
    },
    onError: (err) => onError(errorMessage(err, 'Reset failed')),
  });

  const setSecret = useMutation({
    mutationFn: (secret: string) => putConfig(setting.key, secret),
    onSuccess: () => {
      onError(undefined);
      onSaved();
    },
    onError: (err) => onError(errorMessage(err, 'Save failed')),
  });

  const busy = save.isPending || reset.isPending || setSecret.isPending;
  const hint = rangeHint(setting);

  return (
    <div
      className={`rounded-xl border p-3 transition ${
        dirty ? 'border-brand-600/60 bg-brand-600/5' : 'border-slate-800 bg-slate-900/30'
      }`}
      data-testid={`setting-${setting.key}`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-slate-100">{setting.label ?? setting.key}</span>
        <SourceBadge source={setting.source} />
        {setting.locked ? <Badge tone="danger">read-only</Badge> : null}
        {setting.restart_required ? (
          <span title="Applies on restart, or to newly-created sandboxes">
            <Badge tone="warning">restart required</Badge>
          </span>
        ) : null}
        {dirty ? <Badge tone="brand">unsaved</Badge> : null}
        {showCategory ? <span className="text-xs text-slate-600">in {showCategory}</span> : null}
      </div>

      <code className="mt-1 block font-mono text-[11px] text-slate-500">
        {setting.env_var ?? setting.key.toUpperCase()}
      </code>
      <p className="mt-1 text-xs text-slate-400">{setting.description}</p>

      {setting.locked ? (
        <div className="mt-2 space-y-1">
          <span
            data-testid={`locked-value-${setting.key}`}
            className="inline-block rounded-lg border border-slate-800 bg-slate-950 px-2.5 py-1.5 font-mono text-xs text-slate-400"
          >
            {displayValue(setting)}
          </span>
          <p className="text-xs text-amber-300/80">{setting.locked_reason}</p>
        </div>
      ) : setting.sensitive ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <SensitiveField
            isSet={Boolean(setting.value)}
            busy={setSecret.isPending}
            onSubmit={(secret) => setSecret.mutate(secret)}
            note={
              setting.source === 'db'
                ? 'Set from this panel — encrypted at rest, and never shown again.'
                : 'Currently coming from the environment. Setting it here overrides that.'
            }
          />
          <Button
            size="sm"
            variant="ghost"
            disabled={busy || !canReset}
            title={canReset ? 'Remove the override and fall back to env/default' : 'Not overridden'}
            data-testid={`reset-${setting.key}`}
            onClick={() => reset.mutate()}
          >
            Reset
          </Button>
        </div>
      ) : (
        <>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Editor
              setting={setting}
              draft={current}
              onDraft={(v) => onDraft(v === serverDraft(setting) ? undefined : v)}
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
              title={
                canReset ? 'Remove the override and fall back to env/default' : 'Not overridden'
              }
              data-testid={`reset-${setting.key}`}
              onClick={() => reset.mutate()}
            >
              Reset
            </Button>
          </div>
          <div className="mt-1.5 flex flex-wrap gap-3 text-[11px] text-slate-600">
            <span>
              code default: <span className="font-mono">{String(setting.default ?? '') || '—'}</span>
            </span>
            {hint ? <span>allowed: {hint}</span> : null}
            {setting.choices.length > 0 ? <span>one of: {setting.choices.join(' · ')}</span> : null}
          </div>
        </>
      )}

      {error ? (
        <p className="mt-2 text-xs text-red-400" data-testid={`error-${setting.key}`}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The catalog editor (phase-51/52).
 *
 * Every row shows where its value comes from (`admin > env > default`) so precedence is visible,
 * and edits can be saved one at a time or in one batch — with ~110 settings on the platform, a
 * per-row-only save would make a real reconfiguration tedious. Secrets never join the batch: they
 * are write-only, submitted on their own, and never held in form state.
 */
export function SettingsForm({
  settings,
  onChanged,
  categoryLabels,
}: {
  settings: SettingView[];
  onChanged: () => void;
  /** When set, each row is tagged with its section (used by cross-section search). */
  categoryLabels?: Record<string, string>;
}): JSX.Element {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});

  const dirty = useMemo(
    () => settings.filter((s) => s.key in drafts && drafts[s.key] !== serverDraft(s)),
    [settings, drafts],
  );

  const setDraft = (key: string, value: Draft | undefined): void =>
    setDrafts((prev) => {
      const next = { ...prev };
      if (value === undefined) delete next[key];
      else next[key] = value;
      return next;
    });

  const setError = (key: string, message: string | undefined): void =>
    setErrors((prev) => {
      const next = { ...prev };
      if (message === undefined) delete next[key];
      else next[key] = message;
      return next;
    });

  const saveAll = useMutation({
    mutationFn: () =>
      putConfigBulk(Object.fromEntries(dirty.map((s) => [s.key, toValue(s, drafts[s.key])]))),
    onSuccess: (result) => {
      setErrors(result.errors);
      setDrafts((prev) => {
        const next = { ...prev };
        // Keep the drafts the server rejected so the operator can fix them in place.
        result.updated.forEach((v) => delete next[v.key]);
        return next;
      });
      onChanged();
    },
    onError: (err) =>
      setErrors({ __form: errorMessage(err, 'Could not save these changes') }),
  });

  if (settings.length === 0) {
    return <p className="text-sm text-slate-500">No settings match.</p>;
  }

  return (
    <div className="space-y-2" data-testid="settings-form">
      {settings.map((s) => (
        <SettingRow
          key={s.key}
          setting={s}
          draft={drafts[s.key]}
          onDraft={(v) => setDraft(s.key, v)}
          error={errors[s.key]}
          onError={(message) => setError(s.key, message)}
          onSaved={onChanged}
          showCategory={categoryLabels?.[s.category]}
        />
      ))}

      {errors.__form ? (
        <p className="text-xs text-red-400" data-testid="bulk-error">
          {errors.__form}
        </p>
      ) : null}

      {dirty.length > 0 ? (
        <div
          className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand-600/50 bg-slate-900/95 px-3 py-2 backdrop-blur"
          data-testid="bulk-bar"
        >
          <span className="text-sm text-slate-300">
            {dirty.length} unsaved {dirty.length === 1 ? 'change' : 'changes'}
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              data-testid="discard-all"
              disabled={saveAll.isPending}
              onClick={() => {
                setDrafts({});
                setErrors({});
              }}
            >
              Discard
            </Button>
            <Button
              size="sm"
              data-testid="save-all"
              loading={saveAll.isPending}
              onClick={() => saveAll.mutate()}
            >
              Save {dirty.length}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default SettingsForm;
