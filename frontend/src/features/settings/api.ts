import { apiFetch } from '../../lib/apiClient';
import type { CredentialDto, CredentialKind } from '../../lib/types';

/** Metadata for the current user's stored credentials — values are never returned (phase-34). */
export function listCredentials(): Promise<CredentialDto[]> {
  return apiFetch<CredentialDto[]>('/credentials');
}

/** Add or replace a BYO token. Write-only: the secret goes up and can never be read back. */
export function putCredential(kind: CredentialKind, secret: string): Promise<CredentialDto> {
  return apiFetch<CredentialDto>(`/credentials/${kind}`, {
    method: 'PUT',
    body: JSON.stringify({ secret }),
  });
}

export function deleteCredential(kind: CredentialKind): Promise<{ deleted: boolean }> {
  return apiFetch<{ deleted: boolean }>(`/credentials/${kind}`, { method: 'DELETE' });
}
