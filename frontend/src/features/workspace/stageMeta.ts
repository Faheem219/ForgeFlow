// Stage presentation metadata + the (only two) hard-prerequisite rules from §8 / phase-06.

import type { Stage, StageStatus } from '../../lib/types';

export const STAGE_ORDER: Stage[] = [
  'design',
  'requirements',
  'build',
  'test',
  'deploy',
  'validate',
];

export const STAGE_LABELS: Record<Stage, string> = {
  design: 'Design',
  requirements: 'Requirements',
  build: 'Build',
  test: 'Test',
  deploy: 'Deploy',
  validate: 'Validate',
};

/** The only mandatory ordering: deploy needs a completed build, validate a completed deploy. */
export const HARD_PREREQ: Partial<Record<Stage, Stage>> = {
  deploy: 'build',
  validate: 'deploy',
};

export const STATUS_LABELS: Record<StageStatus, string> = {
  empty: 'Not started',
  in_progress: 'In progress',
  awaiting_user: 'Needs you',
  complete: 'Complete',
  skipped: 'Skipped',
  stale: 'Stale',
};

type Tone = 'neutral' | 'brand' | 'success' | 'warning' | 'danger';

export const STATUS_TONES: Record<StageStatus, Tone> = {
  empty: 'neutral',
  in_progress: 'brand',
  awaiting_user: 'warning',
  complete: 'success',
  skipped: 'neutral',
  stale: 'warning',
};

/**
 * A stage is "locked" when its hard prerequisite is not complete. The lock is a *hint* only —
 * the conductor is the single owner of legality and rejects illegal intents server-side.
 */
export function prereqUnmet(
  stage: Stage,
  statusByStage: Partial<Record<Stage, StageStatus>>,
): Stage | null {
  const prereq = HARD_PREREQ[stage];
  if (prereq && statusByStage[prereq] !== 'complete') {
    return prereq;
  }
  return null;
}
