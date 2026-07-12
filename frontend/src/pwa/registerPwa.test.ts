import { afterEach, describe, expect, it, vi } from 'vitest';

import { useToastStore } from '../lib/stores/toastStore';
import { registerPwa, type RegisterSW, type RegisterSWOptions } from './registerPwa';

afterEach(() => useToastStore.setState({ toasts: [] }));

describe('registerPwa', () => {
  it('registers immediately and offers a reload when an update is ready', () => {
    let captured: RegisterSWOptions | undefined;
    const updateSW = vi.fn(() => Promise.resolve());
    const register: RegisterSW = (options) => {
      captured = options;
      return updateSW;
    };

    registerPwa(register);
    expect(captured?.immediate).toBe(true);

    // Simulate the SW signalling a new version.
    captured?.onNeedRefresh?.();
    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0].title).toBe('Update available');

    toasts[0].action?.onClick();
    expect(updateSW).toHaveBeenCalledWith(true);
  });

  it('announces offline readiness', () => {
    const register: RegisterSW = (options) => {
      options?.onOfflineReady?.();
      return () => Promise.resolve();
    };
    registerPwa(register);
    expect(useToastStore.getState().toasts[0]?.title).toBe('Ready to work offline');
  });
});
