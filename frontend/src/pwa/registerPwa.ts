import { toast } from '../lib/stores/toastStore';

export interface RegisterSWOptions {
  immediate?: boolean;
  onNeedRefresh?: () => void;
  onOfflineReady?: () => void;
  onRegisteredSW?: (
    swScriptUrl: string,
    registration: ServiceWorkerRegistration | undefined,
  ) => void;
  onRegisterError?: (error: unknown) => void;
}

/** Matches the `registerSW` export of `virtual:pwa-register`. */
export type RegisterSW = (options?: RegisterSWOptions) => (reloadPage?: boolean) => Promise<void>;

/**
 * Wire the service-worker update flow: when a new version is ready, show a toast with a
 * "Reload" action. Conservative app-shell caching only (aggressive strategies are phase-49).
 */
export function registerPwa(registerSW: RegisterSW): void {
  let updateSW: (reloadPage?: boolean) => Promise<void> = async () => {};
  updateSW = registerSW({
    immediate: true,
    onNeedRefresh() {
      toast({
        title: 'Update available',
        description: 'A new version of ForgeFlow is ready.',
        variant: 'info',
        durationMs: 0,
        action: { label: 'Reload', onClick: () => void updateSW(true) },
      });
    },
    onOfflineReady() {
      toast({ title: 'Ready to work offline', variant: 'success' });
    },
  });
}
