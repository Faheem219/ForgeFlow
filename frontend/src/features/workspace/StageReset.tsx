import { Undo2 } from 'lucide-react';
import { useState } from 'react';

import { Button, ConfirmDialog } from '../../components/ui';
import type { Stage, StageStatus } from '../../lib/types';
import { canReset, resetHint } from './stageMeta';
import { resetNeedsConfirm, useStageReset } from './useStageReset';

/** The confirmation for resetting Deploy — the one reset that destroys something. */
export function ResetDeployDialog({
  open,
  onClose,
  onConfirm,
  loading,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  loading?: boolean;
}): JSX.Element {
  return (
    <ConfirmDialog
      open={open}
      onClose={onClose}
      onConfirm={onConfirm}
      loading={loading}
      title="Reset Deploy?"
      confirmLabel="Reset and take it down"
      description={
        <>
          The app&apos;s hosting is deleted at the provider (on Vercel, its frontend and backend
          projects) and its URLs stop working. Deploy and Validate go back to{' '}
          <strong className="font-medium text-fg">Not started</strong>, as if the app had never been
          deployed.
        </>
      }
    >
      <ul className="list-disc space-y-1 rounded-lg bg-fg/[0.04] py-2.5 pl-7 pr-3 text-xs text-fg-muted marker:text-fg-faint">
        <li>Your app&apos;s database and its data are not touched</li>
        <li>Build and Test keep their status and their work</li>
        <li>You can deploy again straight away</li>
      </ul>
    </ConfirmDialog>
  );
}

/**
 * "Reset" for a stage panel's header: takes back a "done" without re-running anything.
 *
 * Renders nothing when there is nothing to reset, so each panel can place it unconditionally.
 */
export function ResetStageButton({
  projectId,
  stage,
  status,
  disabled,
}: {
  projectId: string;
  stage: Stage;
  status: StageStatus;
  disabled?: boolean;
}): JSX.Element | null {
  const [confirming, setConfirming] = useState(false);
  const reset = useStageReset(projectId);

  if (!canReset(stage, status)) return null;

  return (
    <>
      <Button
        size="sm"
        variant="ghost"
        disabled={disabled || reset.isPending}
        title={resetHint(stage)}
        data-testid={`${stage}-reset`}
        onClick={() => (resetNeedsConfirm(stage) ? setConfirming(true) : reset.mutate(stage))}
      >
        <Undo2 aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
        Reset
      </Button>
      {resetNeedsConfirm(stage) ? (
        <ResetDeployDialog
          open={confirming}
          loading={reset.isPending}
          onClose={() => setConfirming(false)}
          onConfirm={() => reset.mutate(stage, { onSettled: () => setConfirming(false) })}
        />
      ) : null}
    </>
  );
}
