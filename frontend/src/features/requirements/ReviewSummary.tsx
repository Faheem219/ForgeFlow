import { Badge, Button } from '../../components/ui';
import type { CriterionKind, RequirementSpecDto } from '../../lib/types';

const KIND_TONES: Record<CriterionKind, 'brand' | 'success' | 'neutral'> = {
  unit: 'brand',
  e2e: 'success',
  either: 'neutral',
};

/**
 * Read-only view of a saved spec (features → inputs/behaviors/criteria) with an Edit affordance.
 * "Save" happens in the form; this is the review half of the capture → review loop (D6).
 */
export function ReviewSummary({
  spec,
  onEdit,
}: {
  spec: RequirementSpecDto;
  onEdit: () => void;
}): JSX.Element {
  return (
    <div className="space-y-4" data-testid="review-summary">
      <div className="flex items-center justify-between">
        <p className="text-xs uppercase tracking-wide text-fg-subtle">
          {spec.features.length} feature{spec.features.length === 1 ? '' : 's'} · version{' '}
          <span data-testid="review-version">{spec.version}</span>
        </p>
        <Button size="sm" variant="secondary" data-testid="review-edit" onClick={onEdit}>
          Edit
        </Button>
      </div>

      {spec.features.map((f, fi) => (
        <section
          key={f.name}
          data-testid={`review-feature-${fi}`}
          className="space-y-2 rounded-xl border border-edge bg-surface p-4"
        >
          <h4 className="text-sm font-medium text-fg">{f.name}</h4>
          {f.description ? <p className="text-sm text-fg-muted">{f.description}</p> : null}

          {f.inputs.length > 0 ? (
            <p className="text-xs text-fg-muted">
              <span className="text-fg-subtle">Inputs:</span> {f.inputs.join(', ')}
            </p>
          ) : null}
          {f.expected_behaviors.length > 0 ? (
            <p className="text-xs text-fg-muted">
              <span className="text-fg-subtle">Behaviors:</span> {f.expected_behaviors.join(', ')}
            </p>
          ) : null}

          <ul className="space-y-1">
            {f.acceptance_criteria.map((c) => (
              <li key={c.id} className="flex items-center gap-2 text-sm text-fg">
                <Badge tone={KIND_TONES[c.kind]}>{c.kind}</Badge>
                <span>{c.text}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export default ReviewSummary;
