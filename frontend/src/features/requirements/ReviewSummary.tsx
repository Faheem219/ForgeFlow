import { Pencil } from 'lucide-react';

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
 *
 * Laid out as a document rather than a stack of identical cards: each feature leads with what it
 * is, its inputs and behaviours sit in a definition grid, and the acceptance criteria — the part the
 * tests are generated from — get their own band beneath.
 */
export function ReviewSummary({
  spec,
  onEdit,
}: {
  spec: RequirementSpecDto;
  onEdit: () => void;
}): JSX.Element {
  return (
    <div className="space-y-3" data-testid="review-summary">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-fg-subtle">
          {spec.features.length} feature{spec.features.length === 1 ? '' : 's'} · version{' '}
          <span data-testid="review-version" className="tabular-nums">
            {spec.version}
          </span>
        </p>
        <Button size="sm" variant="secondary" data-testid="review-edit" onClick={onEdit}>
          <Pencil aria-hidden className="h-3 w-3" strokeWidth={2} />
          Edit
        </Button>
      </div>

      {spec.features.map((f, fi) => (
        <section
          key={f.name}
          data-testid={`review-feature-${fi}`}
          className="overflow-hidden rounded-xl border border-edge bg-surface"
        >
          <div className="px-4 pb-3 pt-3.5">
            <h4 className="text-sm font-semibold text-fg">{f.name}</h4>
            {f.description ? (
              <p className="mt-0.5 text-[13px] leading-relaxed text-fg-muted">{f.description}</p>
            ) : null}

            {f.inputs.length > 0 || f.expected_behaviors.length > 0 ? (
              <dl className="mt-3 grid gap-x-4 gap-y-1.5 text-xs sm:grid-cols-[5.5rem_minmax(0,1fr)]">
                {f.inputs.length > 0 ? (
                  <>
                    <dt className="text-fg-subtle">Inputs</dt>
                    <dd className="text-fg-muted">{f.inputs.join(', ')}</dd>
                  </>
                ) : null}
                {f.expected_behaviors.length > 0 ? (
                  <>
                    <dt className="text-fg-subtle">Behaviors</dt>
                    <dd className="text-fg-muted">{f.expected_behaviors.join(', ')}</dd>
                  </>
                ) : null}
              </dl>
            ) : null}
          </div>

          <div className="border-t border-edge bg-fg/[0.015] px-4 py-2.5">
            <p className="mb-1.5 text-2xs font-medium text-fg-faint">Acceptance criteria</p>
            <ul className="space-y-1.5">
              {f.acceptance_criteria.map((c) => (
                <li key={c.id} className="flex items-start gap-2.5 text-[13px] text-fg">
                  <span className="mt-px w-12 shrink-0">
                    <Badge tone={KIND_TONES[c.kind]}>{c.kind}</Badge>
                  </span>
                  <span className="leading-snug">{c.text}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      ))}
    </div>
  );
}

export default ReviewSummary;
