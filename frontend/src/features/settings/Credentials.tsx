import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { Badge, Button, EmptyState, Panel, Spinner } from '../../components/ui';
import { ApiError } from '../../lib/apiClient';
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

const inputClass =
  'w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100 outline-none focus:border-brand-500';

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
        <p className="text-sm text-slate-400">
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
            <span className="mb-1 block text-slate-300">Provider</span>
            <select
              className={`${inputClass} w-40`}
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
            <span className="mb-1 block text-slate-300">Token</span>
            <input
              type="password"
              autoComplete="off"
              className={inputClass}
              data-testid="credential-secret"
              value={secret}
              placeholder="Paste the token — it is never shown again"
              onChange={(e) => setSecret(e.target.value)}
            />
          </label>

          <Button type="submit" disabled={busy || !secret.trim()} data-testid="credential-save">
            {save.isPending ? 'Saving…' : 'Save token'}
          </Button>
        </form>

        <div>
          <h4 className="mb-2 text-xs uppercase tracking-wide text-slate-500">Stored</h4>
          {credentialsQuery.isLoading ? (
            <div className="flex justify-center py-4">
              <Spinner />
            </div>
          ) : credentials.length === 0 ? (
            <EmptyState
              title="No tokens stored"
              description="Add a provider token above to use your own account for deploys."
            />
          ) : (
            <ul className="space-y-1" data-testid="credential-list">
              {credentials.map((c) => (
                <li
                  key={`${c.kind}-${c.scope}`}
                  data-testid={`credential-row-${c.kind}`}
                  className="flex items-center justify-between rounded-lg border border-slate-800 px-3 py-2 text-sm"
                >
                  <span className="flex items-center gap-2 text-slate-200">
                    {KIND_LABELS[c.kind]}
                    <Badge tone={c.scope === 'byo' ? 'brand' : 'neutral'}>{c.scope}</Badge>
                    {c.last4 ? (
                      <span className="font-mono text-xs text-slate-500">····{c.last4}</span>
                    ) : null}
                  </span>
                  <span className="flex items-center gap-3">
                    <span className="text-xs text-slate-500">
                      {new Date(c.created_at).toLocaleDateString()}
                    </span>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={busy}
                      data-testid={`credential-delete-${c.kind}`}
                      onClick={() => remove.mutate(c.kind)}
                    >
                      Delete
                    </Button>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

export default Credentials;
