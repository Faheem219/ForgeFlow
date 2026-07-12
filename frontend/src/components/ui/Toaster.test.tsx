import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { toast, useToastStore } from '../../lib/stores/toastStore';
import { Toaster } from './Toaster';

afterEach(() => {
  act(() => useToastStore.setState({ toasts: [] }));
});

describe('Toaster', () => {
  it('renders a pushed toast and dismisses it via the close button', () => {
    render(<Toaster />);
    act(() => {
      toast({ title: 'Saved', durationMs: 0 });
    });
    expect(screen.getByText('Saved')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('Dismiss'));
    expect(screen.queryByText('Saved')).not.toBeInTheDocument();
  });

  it('runs the action then dismisses', () => {
    render(<Toaster />);
    let ran = false;
    act(() => {
      toast({
        title: 'Update',
        durationMs: 0,
        action: { label: 'Reload', onClick: () => (ran = true) },
      });
    });

    fireEvent.click(screen.getByText('Reload'));
    expect(ran).toBe(true);
    expect(screen.queryByText('Update')).not.toBeInTheDocument();
  });
});
