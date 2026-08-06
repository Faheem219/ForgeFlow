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
  'rounded-lg border border-edge-strong bg-surface-sunken px-2.5 py-1.5 text-sm text-fg ' +
  'outline-none focus:border-brand disabled:opacity-50';

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
      <label className="flex items-center gap-2 text-sm text-fg-muted">
        <input
          type="checkbox"
          data-testid={`toggle-${setting.key}`}
          className="h-4 w-4 accent-brand"
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

  /*
   * One row in the grouped list: identity on the left, control on the right. The row is flat —
   * the group carries the border — so ~110 settings read as a list to scan, not a wall of cards.
   * A dirty row is marked by a brand spine + wash rather than swapping its whole chrome.
   */
  return (
    <div
      className={`relative px-4 py-4 transition-colors lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)] lg:gap-6 ${
        dirty ? 'bg-brand/5' : 'hover:bg-surface-raised/40'
      }`}
      data-testid={`setting-${setting.key}`}
    >
      <span
        aria-hidden
        className={`absolute inset-y-2 left-0 w-0.5 rounded-r-full bg-brand transition-opacity ${
          dirty ? 'opacity-100' : 'opacity-0'
        }`}
      />

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-fg">{setting.label ?? setting.key}</span>
          <SourceBadge source={setting.source} />
          {setting.locked ? <Badge tone="danger">read-only</Badge> : null}
          {setting.restart_required ? (
            <span title="Applies on restart, or to newly-created sandboxes">
              <Badge tone="warning">restart required</Badge>
            </span>
          ) : null}
          {dirty ? <Badge tone="brand">unsaved</Badge> : null}
          {showCategory ? <span className="text-xs text-fg-faint">in {showCategory}</span> : null}
        </div>
        <p className="mt-1 text-xs leading-relaxed text-fg-muted">{setting.description}</p>
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-fg-faint">
          <code className="font-mono text-fg-subtle">
            {setting.env_var ?? setting.key.toUpperCase()}
          </code>
          <span>
            default: <span className="font-mono">{String(setting.default ?? '') || '—'}</span>
          </span>
          {hint ? <span>allowed: {hint}</span> : null}
          {setting.choices.length > 0 ? <span>one of: {setting.choices.join(' · ')}</span> : null}
        </div>
      </div>

      <div className="mt-3 lg:mt-0">
        {setting.locked ? (
          <div className="space-y-1">
            <span
              data-testid={`locked-value-${setting.key}`}
              className="inline-block rounded-lg border border-edge bg-surface-sunken px-2.5 py-1.5 font-mono text-xs text-fg-muted"
            >
              {displayValue(setting)}
            </span>
            <p className="text-xs text-warning">{setting.locked_reason}</p>
          </div>
        ) : setting.sensitive ? (
          <div className="flex flex-wrap items-center gap-2">
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
              title={
                canReset ? 'Remove the override and fall back to env/default' : 'Not overridden'
              }
              data-testid={`reset-${setting.key}`}
              onClick={() => reset.mutate()}
            >
              Reset
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
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
        )}

        {error ? (
          <p className="mt-2 text-xs text-danger" data-testid={`error-${setting.key}`}>
            {error}
          </p>
        ) : null}
      </div>
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
    onError: (err) => setErrors({ __form: errorMessage(err, 'Could not save these changes') }),
  });

  if (settings.length === 0) {
    return <p className="text-sm text-fg-subtle">No settings match.</p>;
  }

  return (
    <div className="space-y-3" data-testid="settings-form">
      {/* One bordered group, hairline-divided rows — a list to scan, not a wall of cards. */}
      <div className="divide-y divide-edge overflow-hidden rounded-xl border border-edge bg-surface">
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
      </div>

      {errors.__form ? (
        <p className="text-xs text-danger" data-testid="bulk-error">
          {errors.__form}
        </p>
      ) : null}

      {dirty.length > 0 ? (
        <div
          className="sticky bottom-0 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand/40 bg-surface-overlay/95 px-3 py-2 backdrop-blur"
          data-testid="bulk-bar"
        >
          <span className="text-sm text-fg-muted">
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
