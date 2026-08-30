import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AnimatePresence, motion } from 'framer-motion';
import { KeyRound, Trash2 } from 'lucide-react';
import { useState } from 'react';

import { Badge, Button, EmptyState, Panel, Skeleton, fieldClasses } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
import { formatDateIST } from '../../lib/datetime';
import { DURATION, EASE } from '../../lib/motion';
import { toast } from '../../lib/stores/toastStore';
import type { CredentialKind } from '../../lib/types';
import { deleteCredential, listCredentials, putCredential } from './api';

const KIND_LABELS: Record<CredentialKind, string> = {
  vercel: 'Vercel',
  render: 'Render',
  mongo_uri: 'MongoDB URI',
  stitch: 'Stitch',
  figma: 'Figma',
};

const KINDS = Object.keys(KIND_LABELS) as CredentialKind[];

/**
 * Bring-your-own provider tokens (phase-34). Strictly write-only: the form posts a secret, and the
 * list shows only kind/scope/created/last4 — the API has no route that returns a stored value, so
 * a secret is never held in client state beyond the un-submitted input (cleared on save).
 */
export function Credentials(): JSX.Element {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<CredentialKind>('vercel');
  const [secret, setSecret] = useState('');

  const credentialsQuery = useQuery({ queryKey: ['credentials'], queryFn: listCredentials });

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['credentials'] });
  };

  const save = useMutation({
    mutationFn: () => putCredential(kind, secret.trim()),
    onSuccess: (saved) => {
      setSecret(''); // never keep the plaintext around after it has been stored
      invalidate();
      toast({ title: `${KIND_LABELS[saved.kind]} token saved`, variant: 'success' });
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Could not save the token';
      toast({ title: 'Save failed', description, variant: 'error' });
    },
  });

  const remove = useMutation({
    mutationFn: (k: CredentialKind) => deleteCredential(k),
    onSuccess: () => {
      invalidate();
      toast({ title: 'Token deleted', variant: 'success' });
    },
    onError: (err) => {
      const description = err instanceof ApiError ? err.message : 'Could not delete the token';
      toast({ title: 'Delete failed', description, variant: 'error' });
    },
  });

  const credentials = credentialsQuery.data ?? [];
  const busy = save.isPending || remove.isPending;

  return (
    <Panel title="Provider credentials">
      <div className="space-y-4" data-testid="credentials-panel">
        <p className="text-sm text-fg-muted">
          Your own provider tokens. They are encrypted before storage and used only when ForgeFlow
          calls that provider on your behalf — they are never displayed again after saving.
        </p>

        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (secret.trim()) save.mutate();
          }}
        >
          <label className="text-sm">
            <span className="mb-1 block text-fg-muted">Provider</span>
            <select
              className={`${fieldClasses} w-40`}
              data-testid="credential-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value as CredentialKind)}
            >
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {KIND_LABELS[k]}
                </option>
              ))}
            </select>
          </label>

          <label className="min-w-[16rem] flex-1 text-sm">
            <span className="mb-1 block text-fg-muted">Token</span>
            <input
              type="password"
              autoComplete="off"
              className={fieldClasses}
              data-testid="credential-secret"
              value={secret}
              placeholder="Paste the token — it is never shown again"
              onChange={(e) => setSecret(e.target.value)}
            />
          </label>

          <Button
            type="submit"
            loading={save.isPending}
            disabled={busy || !secret.trim()}
            data-testid="credential-save"
          >
            {save.isPending ? 'Saving…' : 'Save token'}
          </Button>
        </form>

        <div>
          <h4 className="mb-2 text-xs uppercase tracking-wide text-fg-subtle">Stored</h4>
          {credentialsQuery.isLoading ? (
            // Ghosts with the same bones as real rows, so the resolve never jumps the layout.
            <div className="space-y-1" aria-hidden>
              {Array.from({ length: 2 }, (_, i) => (
                <div
                  key={i}
                  className="flex items-center gap-3 rounded-lg border border-edge px-3 py-2"
                >
                  <Skeleton className="h-7 w-7 rounded-lg" />
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="ml-auto h-4 w-16" />
                </div>
              ))}
            </div>
          ) : credentials.length === 0 ? (
            <EmptyState
              icon={<KeyRound aria-hidden className="h-6 w-6" strokeWidth={1.5} />}
              title="No tokens stored"
              description="Add a provider token above to use your own account for deploys."
            />
          ) : (
            <ul className="space-y-1" data-testid="credential-list">
              <AnimatePresence initial={false}>
                {credentials.map((c) => (
                  <motion.li
                    key={`${c.kind}-${c.scope}`}
                    layout
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, x: -12 }}
                    transition={{ duration: DURATION.base, ease: EASE }}
                    data-testid={`credential-row-${c.kind}`}
                    className="group flex items-center justify-between rounded-lg border border-edge px-3 py-2 text-sm transition-colors hover:border-edge-strong hover:bg-surface-raised/50"
                  >
                    <span className="flex items-center gap-2.5 text-fg">
                      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-brand/20 bg-brand/10 text-brand-text">
                        <KeyRound aria-hidden className="h-3.5 w-3.5" strokeWidth={1.75} />
                      </span>
                      <span className="font-medium">{KIND_LABELS[c.kind]}</span>
                      <Badge tone={c.scope === 'byo' ? 'brand' : 'neutral'}>{c.scope}</Badge>
                      {c.last4 ? (
                        <span className="font-mono text-xs text-fg-subtle">····{c.last4}</span>
                      ) : null}
                    </span>
                    <span className="flex items-center gap-3">
                      <span className="text-xs text-fg-subtle">{formatDateIST(c.created_at)}</span>
                      <button
                        type="button"
                        disabled={busy}
                        aria-label={`Delete ${KIND_LABELS[c.kind]} token`}
                        data-testid={`credential-delete-${c.kind}`}
                        onClick={() => remove.mutate(c.kind)}
                        className="rounded-md p-1.5 text-fg-subtle transition-colors hover:bg-danger/10 hover:text-danger disabled:pointer-events-none disabled:opacity-50"
                      >
                        <Trash2 aria-hidden className="h-4 w-4" strokeWidth={1.75} />
                      </button>
                    </span>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

export default Credentials;
