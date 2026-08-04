import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';

import { Button } from './Button';
import { Input } from './Input';
import { Modal } from './Modal';

/**
 * Confirmation for an irreversible action.
 *
 * When `confirmPhrase` is given the user must type it exactly before the destructive button
 * enables. That friction is the point: deleting a project takes its sandbox, its database and its
 * whole history with it, and an "Are you sure? [OK]" is not a decision — it is a reflex.
 *
 * The dialog is deliberately NOT dismissible while the action is in flight; a half-run cascade the
 * user thinks they cancelled is worse than a two-second wait.
 */
export interface ConfirmDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  description?: ReactNode;
  /** Exact text the user must type to enable confirmation. */
  confirmPhrase?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Extra content between the description and the confirmation field (e.g. what will be removed). */
  children?: ReactNode;
  loading?: boolean;
  tone?: 'danger' | 'primary';
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  description,
  confirmPhrase,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  children,
  loading = false,
  tone = 'danger',
}: ConfirmDialogProps): JSX.Element {
  const [typed, setTyped] = useState('');

  // Reset between openings, otherwise a previously-typed phrase would pre-arm the next dialog.
  useEffect(() => {
    if (!open) setTyped('');
  }, [open]);

  const phraseOk = !confirmPhrase || typed.trim() === confirmPhrase;
  const canConfirm = phraseOk && !loading;

  return (
    <Modal
      open={open}
      onClose={loading ? () => undefined : onClose}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            variant={tone === 'danger' ? 'danger' : 'primary'}
            onClick={onConfirm}
            loading={loading}
            disabled={!canConfirm}
            data-testid="confirm-dialog-confirm"
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        {description ? <div className="text-sm text-fg-muted">{description}</div> : null}
        {children}
        {confirmPhrase ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (canConfirm) onConfirm();
            }}
          >
            <Input
              label={`Type “${confirmPhrase}” to confirm`}
              value={typed}
              autoComplete="off"
              spellCheck={false}
              disabled={loading}
              onChange={(e) => setTyped(e.target.value)}
              data-testid="confirm-dialog-phrase"
            />
          </form>
        ) : null}
      </div>
    </Modal>
  );
}
