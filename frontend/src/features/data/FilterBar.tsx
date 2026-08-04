import { Button, Input } from '../../components/ui';
import { FILTER_OPS, type FilterDraft } from './filters';

/** Field / operator / value — a small safe vocabulary rather than a raw query box. */
export function FilterBar({
  draft,
  fields,
  onChange,
  onApply,
  onClear,
  active,
}: {
  draft: FilterDraft;
  fields: string[];
  onChange: (draft: FilterDraft) => void;
  onApply: () => void;
  onClear: () => void;
  active: boolean;
}): JSX.Element {
  const needsValue = draft.op !== 'exists';

  return (
    <form
      className="flex flex-wrap items-end gap-2"
      data-testid="filter-bar"
      onSubmit={(e) => {
        e.preventDefault();
        onApply();
      }}
    >
      <div className="min-w-[8rem] flex-1">
        <label className="mb-1 block text-[11px] uppercase tracking-wide text-fg-subtle">
          Field
          <input
            list="data-fields"
            aria-label="Filter field"
            data-testid="filter-field"
            value={draft.field}
            onChange={(e) => onChange({ ...draft, field: e.target.value })}
            placeholder="e.g. title"
            className="mt-1 w-full rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1.5 text-sm text-fg outline-none focus:border-brand"
          />
        </label>
        <datalist id="data-fields">
          {fields.map((f) => (
            <option key={f} value={f} />
          ))}
        </datalist>
      </div>

      <label className="text-[11px] uppercase tracking-wide text-fg-subtle">
        Operator
        <select
          aria-label="Filter operator"
          data-testid="filter-op"
          value={draft.op}
          onChange={(e) => onChange({ ...draft, op: e.target.value as FilterDraft['op'] })}
          className="mt-1 block rounded-lg border border-edge-strong bg-surface-sunken px-2 py-1.5 text-sm text-fg outline-none focus:border-brand"
        >
          {FILTER_OPS.map((op) => (
            <option key={op.value} value={op.value}>
              {op.label}
            </option>
          ))}
        </select>
      </label>

      {needsValue ? (
        <div className="min-w-[8rem] flex-1">
          <label className="mb-1 block text-[11px] uppercase tracking-wide text-fg-subtle">
            Value
            <Input
              aria-label="Filter value"
              data-testid="filter-value"
              value={draft.value}
              onChange={(e) => onChange({ ...draft, value: e.target.value })}
              placeholder="e.g. true, 42, text"
              className="mt-1 py-1.5"
            />
          </label>
        </div>
      ) : null}

      <Button size="sm" type="submit" data-testid="filter-apply">
        Filter
      </Button>
      {active ? (
        <Button size="sm" variant="secondary" onClick={onClear} data-testid="filter-clear">
          Clear
        </Button>
      ) : null}
    </form>
  );
}

export default FilterBar;
