import { Button, Input, fieldClasses } from '../../components/ui';
import { cn } from '../../lib/cn';
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
      {/* One structure for all three fields — label stacked over an h-8 control — so their
          labels share a baseline and their boxes share a height. */}
      <label className="flex min-w-[8rem] flex-1 flex-col gap-1 text-2xs font-medium text-fg-subtle">
        Field
        <input
          list="data-fields"
          aria-label="Filter field"
          data-testid="filter-field"
          value={draft.field}
          onChange={(e) => onChange({ ...draft, field: e.target.value })}
          placeholder="e.g. title"
          className={cn(fieldClasses, 'h-8 py-0 font-mono text-xs')}
        />
      </label>
      <datalist id="data-fields">
        {fields.map((f) => (
          <option key={f} value={f} />
        ))}
      </datalist>

      <label className="flex flex-col gap-1 text-2xs font-medium text-fg-subtle">
        Operator
        <select
          aria-label="Filter operator"
          data-testid="filter-op"
          value={draft.op}
          onChange={(e) => onChange({ ...draft, op: e.target.value as FilterDraft['op'] })}
          className={cn(fieldClasses, 'h-8 w-auto py-0 text-xs')}
        >
          {FILTER_OPS.map((op) => (
            <option key={op.value} value={op.value}>
              {op.label}
            </option>
          ))}
        </select>
      </label>

      {needsValue ? (
        <label className="flex min-w-[8rem] flex-1 flex-col gap-1 text-2xs font-medium text-fg-subtle">
          Value
          <Input
            aria-label="Filter value"
            data-testid="filter-value"
            value={draft.value}
            onChange={(e) => onChange({ ...draft, value: e.target.value })}
            placeholder="e.g. true, 42, text"
            className="h-8 py-0 font-mono text-xs placeholder:font-sans"
          />
        </label>
      ) : null}

      <Button size="md" type="submit" data-testid="filter-apply">
        Filter
      </Button>
      {active ? (
        <Button size="md" variant="ghost" onClick={onClear} data-testid="filter-clear">
          Clear
        </Button>
      ) : null}
    </form>
  );
}

export default FilterBar;
