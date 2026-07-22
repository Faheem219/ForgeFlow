import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { Toaster } from '../components/ui';
import { useToastStore } from '../lib/stores/toastStore';
import { registerPwa, type RegisterSW, type RegisterSWOptions } from './registerPwa';

afterEach(() => useToastStore.setState({ toasts: [] }));

/** Wire up registerPwa and hand back the captured options + the update spy it produced. */
function setup(): { options: RegisterSWOptions | undefined; updateSW: ReturnType<typeof vi.fn> } {
  let options: RegisterSWOptions | undefined;
  const updateSW = vi.fn(() => Promise.resolve());
  const register: RegisterSW = (opts) => {
    options = opts;
    return updateSW;
  };
  registerPwa(register);
  return { options, updateSW };
}

describe('service-worker update flow', () => {
  it('shows a persistent "Update available" prompt when a new SW is waiting', () => {
    const { options } = setup();
    render(<Toaster />);

    // No prompt until the SW signals a new version.
    expect(screen.queryByText('Update available')).not.toBeInTheDocument();

    act(() => options?.onNeedRefresh?.());

    expect(screen.getByText('Update available')).toBeInTheDocument();
    // The prompt must not auto-dismiss — the user has to choose to reload.
    const [toast] = useToastStore.getState().toasts;
    expect(toast.title).toBe('Update available');
  });

  it('activates the new worker and reloads when the user clicks Reload', async () => {
    const { options, updateSW } = setup();
    render(<Toaster />);

    act(() => options?.onNeedRefresh?.());
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));

    // updateSW(true) = skip waiting + reload the page onto the new version.
    await waitFor(() => expect(updateSW).toHaveBeenCalledWith(true));
  });

  it('registers immediately so the check happens on load', () => {
    const { options } = setup();
    expect(options?.immediate).toBe(true);
  });

  it('confirms offline readiness once the shell is precached', () => {
    const register: RegisterSW = (opts) => {
      opts?.onOfflineReady?.();
      return () => Promise.resolve();
    };
    registerPwa(register);

    render(<Toaster />);
    expect(screen.getByText('Ready to work offline')).toBeInTheDocument();
  });

  it('does not prompt for an update on a normal first load', () => {
    setup();
    render(<Toaster />);
    // Without an onNeedRefresh signal, the user is never nagged.
    expect(screen.queryByText('Update available')).not.toBeInTheDocument();
  });
});
