import { useMemo, useState } from 'react';

import { Button, Spinner } from '../../components/ui';
import type {
  CriterionKind,
  DraftedFeature,
  DraftedSpec,
  RequirementSpecInput,
  SuggestedCriterion,
} from '../../lib/types';

// ------------------------------------------------------------------ draft model (client-only keys)

let _seq = 0;
const uid = (): string => `k${(_seq += 1)}`;

interface DraftItem {
  key: string;
  value: string;
}
interface DraftCriterion {
  key: string;
  id: string | null;
  text: string;
  kind: CriterionKind;
}
interface DraftFeature {
  key: string;
  name: string;
  description: string;
  inputs: DraftItem[];
  behaviors: DraftItem[];
  criteria: DraftCriterion[];
}

const item = (value = ''): DraftItem => ({ key: uid(), value });
const criterion = (
  text = '',
  kind: CriterionKind = 'either',
  id: string | null = null,
): DraftCriterion => ({
  key: uid(),
  id,
  text,
  kind,
});
const feature = (): DraftFeature => ({
  key: uid(),
  name: '',
  description: '',
  inputs: [],
  behaviors: [],
  criteria: [criterion()],
});

/** Is this row still untouched? Blank rows are dropped when a draft lands, so it stands alone. */
function isBlank(f: DraftFeature): boolean {
  return (
    !f.name.trim() &&
    !f.description.trim() &&
    !f.inputs.some((i) => i.value.trim()) &&
    !f.behaviors.some((b) => b.value.trim()) &&
    !f.criteria.some((c) => c.text.trim())
  );
}

/** An AI-drafted feature → an editable, removable, unsaved row (no criterion ids: nothing saved). */
function fromDraft(d: DraftedFeature): DraftFeature {
  return {
    key: uid(),
    name: d.name,
    description: d.description,
    inputs: d.inputs.map((v) => item(v)),
    behaviors: d.expected_behaviors.map((v) => item(v)),
    criteria:
      d.acceptance_criteria.length > 0
        ? d.acceptance_criteria.map((c) => criterion(c.text, c.kind))
        : [criterion()],
  };
}

function seed(initial: RequirementSpecInput | null): DraftFeature[] {
  if (!initial || initial.features.length === 0) return [feature()];
  return initial.features.map((f) => ({
    key: uid(),
    name: f.name,
    description: f.description,
    inputs: f.inputs.map((v) => item(v)),
    behaviors: f.expected_behaviors.map((v) => item(v)),
    criteria:
      f.acceptance_criteria.length > 0
        ? f.acceptance_criteria.map((c) => criterion(c.text, c.kind, c.id ?? null))
        : [criterion()],
  }));
}

// ------------------------------------------------------------------ validation (mirrors the API)

interface FeatureErrors {
  name?: string;
  criteria?: string;
  criterionText: Record<string, string>;
}
interface FormErrors {
  form?: string;
  features: Record<string, FeatureErrors>;
}

function draftsToInput(features: DraftFeature[], appName: string): RequirementSpecInput {
  return {
    app_name: appName.trim(),
    features: features.map((f) => ({
      name: f.name.trim(),
      description: f.description.trim(),
      inputs: f.inputs.map((i) => i.value.trim()).filter(Boolean),
      expected_behaviors: f.behaviors.map((b) => b.value.trim()).filter(Boolean),
      acceptance_criteria: f.criteria.map((c) => ({
        id: c.id,
        text: c.text.trim(),
        kind: c.kind,
      })),
    })),
  };
}

function validate(features: DraftFeature[]): { errors: FormErrors; ok: boolean } {
  const errors: FormErrors = { features: {} };
  let ok = true;

  if (features.length === 0) {
    errors.form = 'Add at least one feature.';
    return { errors, ok: false };
  }

  const seenNames = new Set<string>();
  for (const f of features) {
    const fe: FeatureErrors = { criterionText: {} };
    const name = f.name.trim();
    if (!name) {
      fe.name = 'Feature name is required.';
      ok = false;
    } else {
      const key = name.toLowerCase();
      if (seenNames.has(key)) {
        fe.name = 'Duplicate feature name.';
        ok = false;
      }
      seenNames.add(key);
    }

    if (f.criteria.length === 0) {
      fe.criteria = 'Add at least one acceptance criterion.';
      ok = false;
    } else {
      for (const c of f.criteria) {
        if (!c.text.trim()) {
          fe.criterionText[c.key] = 'Criterion text is required.';
          ok = false;
        }
      }
    }
    errors.features[f.key] = fe;
  }
  return { errors, ok };
}

const KINDS: CriterionKind[] = ['either', 'unit', 'e2e'];
const KIND_HELP = 'unit = logic a unit test covers · e2e = a user-visible flow · either = unsure';

const inputClass =
  'w-full rounded-lg border border-edge-strong bg-surface-sunken px-3 py-2 text-sm text-fg outline-none focus:border-brand';

// ------------------------------------------------------------------ component

export interface RequirementsFormProps {
  initialSpec: RequirementSpecInput | null;
  onSave: (spec: RequirementSpecInput) => void;
  isSaving?: boolean;
  onCancel?: () => void;
  /** Optional Haiku assist — proposals are appended as editable rows, never overwriting input. */
  onSuggest?: (name: string, description: string) => Promise<SuggestedCriterion[]>;
  onSuggestError?: (message: string, cause?: unknown) => void;
  /** Optional Haiku assist — a freeform description → a whole draft feature list. */
  onDraft?: (description: string) => Promise<DraftedSpec>;
  /**
   * `message` is this form's own fallback wording; `cause` is the rejection behind it (absent when
   * the call merely came back empty). The host reports the server's message when there is one — a
   * generic "could not draft" hides exactly the detail that says how to fix it, e.g. an
   * unconfigured provider key.
   */
  onDraftError?: (message: string, cause?: unknown) => void;
}

/**
 * The guided "fill-in-the-blanks" capture form (D6). Add features → fill name/description/inputs/
 * behaviors/acceptance-criteria (with a `kind` selector) → Save. Validation mirrors the backend so
 * errors surface inline before the round-trip. The optional AI-assist only *appends* proposals.
 *
 * Two AI on-ramps sit alongside the manual path, never replacing it: per-feature "Suggest" proposes
 * acceptance criteria for a feature you have named, and "Generate draft" turns a plain-language
 * description of the whole app into a starting feature list. Both produce ordinary editable rows —
 * nothing is saved until you press Save.
 */
export function RequirementsForm({
  initialSpec,
  onSave,
  isSaving = false,
  onCancel,
  onSuggest,
  onSuggestError,
  onDraft,
  onDraftError,
}: RequirementsFormProps): JSX.Element {
  const [features, setFeatures] = useState<DraftFeature[]>(() => seed(initialSpec));
  const [appName, setAppName] = useState(() => initialSpec?.app_name ?? '');
  const [errors, setErrors] = useState<FormErrors>({ features: {} });
  const [suggesting, setSuggesting] = useState<string | null>(null);
  const [description, setDescription] = useState('');
  const [drafting, setDrafting] = useState(false);

  const errored = useMemo(
    () =>
      Boolean(errors.form) ||
      Object.values(errors.features).some(
        (f) => f.name || f.criteria || Object.keys(f.criterionText).length,
      ),
    [errors],
  );

  const patch = (key: string, fn: (f: DraftFeature) => DraftFeature): void =>
    setFeatures((prev) => prev.map((f) => (f.key === key ? fn(f) : f)));

  const submit = (): void => {
    const result = validate(features);
    setErrors(result.errors);
    if (result.ok) onSave(draftsToInput(features, appName));
  };

  const runSuggest = async (f: DraftFeature): Promise<void> => {
    if (!onSuggest || !f.name.trim()) return;
    setSuggesting(f.key);
    try {
      const proposals = await onSuggest(f.name.trim(), f.description.trim());
      if (proposals.length > 0) {
        patch(f.key, (cur) => {
          // Keep the user's non-blank rows; drop blank starter rows so proposals stand alone.
          const kept = cur.criteria.filter((c) => c.text.trim());
          return {
            ...cur,
            criteria: [...kept, ...proposals.map((p) => criterion(p.text, p.kind))],
          };
        });
      } else {
        onSuggestError?.('No suggestions were returned. Add criteria manually.');
      }
    } catch (err) {
      onSuggestError?.('Could not fetch suggestions. Add criteria manually.', err);
    } finally {
      setSuggesting(null);
    }
  };

  const runDraft = async (): Promise<void> => {
    if (!onDraft || !description.trim()) return;
    setDrafting(true);
    try {
      const drafted = await onDraft(description.trim());
      if (drafted.features.length > 0) {
        // Anything the user already filled in survives; only untouched starter rows give way.
        setFeatures((prev) => [
          ...prev.filter((f) => !isBlank(f)),
          ...drafted.features.map(fromDraft),
        ]);
        // A name the user has already typed is theirs — never overwrite it with a proposal.
        setAppName((cur) => cur.trim() || drafted.app_name);
        setErrors({ features: {} });
      } else {
        onDraftError?.('No draft was returned. Add features manually, or try again.');
      }
    } catch (err) {
      onDraftError?.('Could not draft requirements. Add features manually, or try again.', err);
    } finally {
      setDrafting(false);
    }
  };

  return (
    <div className="space-y-4" data-testid="requirements-form">
      {onDraft ? (
        <section
          className="space-y-2 rounded-xl border border-edge bg-surface-sunken/50 p-4"
          data-testid="requirements-draft"
        >
          <div>
            <h4 className="text-sm font-medium text-fg">Start from a description</h4>
            <p className="text-xs text-fg-subtle">
              Describe the whole app in plain language and we&apos;ll propose a first set of
              features. Everything it suggests is editable — nothing is saved until you press Save.
            </p>
          </div>
          <label className="block text-sm">
            <span className="sr-only">What does your app need to do?</span>
            <textarea
              className={inputClass}
              data-testid="draft-description"
              rows={3}
              value={description}
              placeholder="What does your app need to do? e.g. “A todo app where people sign in, add todos with a due date, and mark them done.”"
              onChange={(e) => setDescription(e.target.value)}
            />
          </label>
          <Button
            size="sm"
            variant="secondary"
            data-testid="draft-generate"
            disabled={!description.trim() || drafting}
            onClick={() => void runDraft()}
          >
            {drafting ? (
              <span className="flex items-center gap-1">
                <Spinner /> Drafting…
              </span>
            ) : (
              'Generate draft (AI)'
            )}
          </Button>
        </section>
      ) : null}

      <section className="space-y-1 rounded-xl border border-edge bg-surface p-4">
        <label className="block text-sm">
          <span className="font-medium text-fg">App name</span>
          <input
            className={inputClass}
            data-testid="app-name"
            value={appName}
            placeholder="e.g. Focus"
            onChange={(e) => setAppName(e.target.value)}
          />
        </label>
        <p className="text-xs text-fg-subtle">
          Optional. Names the app in ForgeFlow and in the design tool, so each project is its own —
          change it whenever you like. Nothing is tested against it.
        </p>
      </section>

      {errors.form ? (
        <p className="text-sm text-rose-400" role="alert" data-testid="form-error">
          {errors.form}
        </p>
      ) : null}

      {features.map((f, fi) => {
        const fe = errors.features[f.key];
        return (
          <section
            key={f.key}
            data-testid={`feature-${fi}`}
            className="space-y-3 rounded-xl border border-edge bg-surface p-4"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1">
                <label className="block text-sm">
                  <span className="mb-1 block text-fg-muted">Feature name</span>
                  <input
                    className={inputClass}
                    data-testid={`feature-name-${fi}`}
                    value={f.name}
                    placeholder="e.g. Todos"
                    onChange={(e) => patch(f.key, (cur) => ({ ...cur, name: e.target.value }))}
                  />
                </label>
                {fe?.name ? (
                  <p
                    className="mt-1 text-xs text-rose-400"
                    data-testid={`feature-name-error-${fi}`}
                  >
                    {fe.name}
                  </p>
                ) : null}
              </div>
              {features.length > 1 ? (
                <Button
                  size="sm"
                  variant="ghost"
                  data-testid={`remove-feature-${fi}`}
                  onClick={() => setFeatures((prev) => prev.filter((x) => x.key !== f.key))}
                >
                  Remove
                </Button>
              ) : null}
            </div>

            <label className="block text-sm">
              <span className="mb-1 block text-fg-muted">Description</span>
              <textarea
                className={inputClass}
                data-testid={`feature-desc-${fi}`}
                rows={2}
                value={f.description}
                placeholder="What should this feature do, in a sentence?"
                onChange={(e) => patch(f.key, (cur) => ({ ...cur, description: e.target.value }))}
              />
            </label>

            <EditableList
              label="Inputs"
              help="Data the user provides (e.g. “todo title”)."
              items={f.inputs}
              testid={`input-${fi}`}
              onChange={(inputs) => patch(f.key, (cur) => ({ ...cur, inputs }))}
            />
            <EditableList
              label="Expected behaviors"
              help="What the user should observe (e.g. “new todo appears in the list”)."
              items={f.behaviors}
              testid={`behavior-${fi}`}
              onChange={(behaviors) => patch(f.key, (cur) => ({ ...cur, behaviors }))}
            />

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-xs uppercase tracking-wide text-fg-subtle">
                  Acceptance criteria
                </span>
                {onSuggest ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    data-testid={`suggest-criteria-${fi}`}
                    disabled={!f.name.trim() || suggesting === f.key}
                    onClick={() => void runSuggest(f)}
                  >
                    {suggesting === f.key ? (
                      <span className="flex items-center gap-1">
                        <Spinner /> Suggesting…
                      </span>
                    ) : (
                      'Suggest (AI)'
                    )}
                  </Button>
                ) : null}
              </div>
              <p className="text-xs text-fg-subtle">{KIND_HELP}</p>
              {fe?.criteria ? (
                <p className="text-xs text-rose-400" data-testid={`criteria-error-${fi}`}>
                  {fe.criteria}
                </p>
              ) : null}

              {f.criteria.map((c, ci) => (
                <div key={c.key} className="space-y-1">
                  <div className="flex items-center gap-2">
                    <input
                      className={inputClass}
                      data-testid={`criterion-text-${fi}-${ci}`}
                      value={c.text}
                      placeholder="e.g. Adding a todo shows it in the list"
                      onChange={(e) =>
                        patch(f.key, (cur) => ({
                          ...cur,
                          criteria: cur.criteria.map((x) =>
                            x.key === c.key ? { ...x, text: e.target.value } : x,
                          ),
                        }))
                      }
                    />
                    <select
                      className={`${inputClass} w-24`}
                      data-testid={`criterion-kind-${fi}-${ci}`}
                      value={c.kind}
                      onChange={(e) =>
                        patch(f.key, (cur) => ({
                          ...cur,
                          criteria: cur.criteria.map((x) =>
                            x.key === c.key ? { ...x, kind: e.target.value as CriterionKind } : x,
                          ),
                        }))
                      }
                    >
                      {KINDS.map((k) => (
                        <option key={k} value={k}>
                          {k}
                        </option>
                      ))}
                    </select>
                    <Button
                      size="sm"
                      variant="ghost"
                      data-testid={`remove-criterion-${fi}-${ci}`}
                      onClick={() =>
                        patch(f.key, (cur) => ({
                          ...cur,
                          criteria: cur.criteria.filter((x) => x.key !== c.key),
                        }))
                      }
                    >
                      ✕
                    </Button>
                  </div>
                  {fe?.criterionText[c.key] ? (
                    <p
                      className="text-xs text-rose-400"
                      data-testid={`criterion-text-error-${fi}-${ci}`}
                    >
                      {fe.criterionText[c.key]}
                    </p>
                  ) : null}
                </div>
              ))}
              <Button
                size="sm"
                variant="secondary"
                data-testid={`add-criterion-${fi}`}
                onClick={() =>
                  patch(f.key, (cur) => ({ ...cur, criteria: [...cur.criteria, criterion()] }))
                }
              >
                + Add criterion
              </Button>
            </div>
          </section>
        );
      })}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button
          variant="secondary"
          data-testid="add-feature"
          onClick={() => setFeatures((prev) => [...prev, feature()])}
        >
          + Add feature
        </Button>
        <div className="flex gap-2">
          {onCancel ? (
            <Button variant="ghost" data-testid="requirements-cancel" onClick={onCancel}>
              Cancel
            </Button>
          ) : null}
          <Button data-testid="requirements-save" disabled={isSaving} onClick={submit}>
            {isSaving ? 'Saving…' : 'Save requirements'}
          </Button>
        </div>
      </div>

      {errored && !errors.form ? (
        <p className="text-right text-xs text-rose-400">Fix the highlighted fields above.</p>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ repeatable string list

function EditableList({
  label,
  help,
  items,
  testid,
  onChange,
}: {
  label: string;
  help: string;
  items: DraftItem[];
  testid: string;
  onChange: (items: DraftItem[]) => void;
}): JSX.Element {
  return (
    <div className="space-y-1">
      <span className="text-xs uppercase tracking-wide text-fg-subtle">{label}</span>
      <p className="text-xs text-fg-subtle">{help}</p>
      {items.map((it, i) => (
        <div key={it.key} className="flex items-center gap-2">
          <input
            className={inputClass}
            data-testid={`feature-${testid}-${i}`}
            value={it.value}
            onChange={(e) =>
              onChange(items.map((x) => (x.key === it.key ? { ...x, value: e.target.value } : x)))
            }
          />
          <Button
            size="sm"
            variant="ghost"
            data-testid={`remove-${testid}-${i}`}
            onClick={() => onChange(items.filter((x) => x.key !== it.key))}
          >
            ✕
          </Button>
        </div>
      ))}
      <Button
        size="sm"
        variant="secondary"
        data-testid={`add-${testid}`}
        onClick={() => onChange([...items, item()])}
      >
        + Add {label.toLowerCase()}
      </Button>
    </div>
  );
}

export default RequirementsForm;
