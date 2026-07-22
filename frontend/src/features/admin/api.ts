import { apiFetch } from '../../lib/apiClient';
import type { ConfigAudit, GlobalCost, SettingView } from './types';

/** The full config catalog with each key's effective value + source (phase-51). */
export function listConfig(): Promise<SettingView[]> {
  return apiFetch<SettingView[]>('/admin/config');
}

/** Write an override → precedence makes it live immediately (unless restart_required). */
export function putConfig(key: string, value: unknown): Promise<SettingView> {
  return apiFetch<SettingView>(`/admin/config/${key}`, {
    method: 'PUT',
    body: JSON.stringify({ value }),
  });
}

/** Remove the override → the key reverts to env/default. */
export function resetConfig(key: string): Promise<{ reverted: SettingView }> {
  return apiFetch<{ reverted: SettingView }>(`/admin/config/${key}`, { method: 'DELETE' });
}

/** Recent config changes (who/what/when, before→after), newest first. */
export function listAudit(): Promise<ConfigAudit[]> {
  return apiFetch<ConfigAudit[]>('/admin/config/audit');
}

/** Platform-wide spend + budget (phase-46). */
export function getGlobalCost(): Promise<GlobalCost> {
  return apiFetch<GlobalCost>('/admin/cost');
}
