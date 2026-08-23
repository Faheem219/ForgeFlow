import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { FocusButton, FocusOverlay } from './FocusOverlay';

/**
 * The load-bearing property here is that the children stay MOUNTED across the transition.
 * Unmounting would be simpler and would silently reload the preview iframe and destroy terminal
 * scrollback — the two things a user is most likely maximizing in order to read.
 */

function Counter({ label }: { label: string }) {
  const [count, setCount] = useState(0);
  return (
    <button type="button" onClick={() => setCount((c) => c + 1)} data-testid="counter">
      {label}: {count}
    </button>
  );
}

function Harness({ startOpen = false }: { startOpen?: boolean }) {
  const [open, setOpen] = useState(startOpen);
  return (
    <div>
      <button type="button" data-testid="outside" onClick={() => setOpen(true)}>
        outside
      </button>
      <FocusOverlay open={open} title="Preview" onClose={() => setOpen(false)}>
        <Counter label="clicks" />
      </FocusOverlay>
    </div>
  );
}

describe('FocusOverlay', () => {
  it('renders children inline when closed', () => {
    render(<Harness />);

    expect(screen.queryByTestId('focus-overlay')).toBeNull();
    expect(screen.getByTestId('counter')).toBeInTheDocument();
  });

  it('promotes children into a dialog when open', () => {
    render(<Harness startOpen />);

    expect(screen.getByTestId('focus-overlay')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('dialog')).toHaveAccessibleName(/Preview/);
  });

  it('keeps the child mounted across open and close, preserving its state', () => {
    render(<Harness />);

    fireEvent.click(screen.getByTestId('counter'));
    fireEvent.click(screen.getByTestId('counter'));
    expect(screen.getByTestId('counter')).toHaveTextContent('clicks: 2');

    fireEvent.click(screen.getByTestId('outside'));
    // Still 2 — a remount would have reset it to 0, which is exactly the iframe/scrollback bug.
    expect(screen.getByTestId('counter')).toHaveTextContent('clicks: 2');

    fireEvent.click(screen.getByTestId('focus-close'));
    expect(screen.getByTestId('counter')).toHaveTextContent('clicks: 2');
  });

  it('closes on Escape', () => {
    render(<Harness startOpen />);

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByTestId('focus-overlay')).toBeNull();
  });

  it('closes from the close button', () => {
    render(<Harness startOpen />);

    fireEvent.click(screen.getByTestId('focus-close'));

    expect(screen.queryByTestId('focus-overlay')).toBeNull();
  });

  it('restores focus to whatever opened it', () => {
    render(<Harness />);
    const opener = screen.getByTestId('outside');

    opener.focus();
    fireEvent.click(opener);
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(document.activeElement).toBe(opener);
  });

  it('traps Tab so focus cannot reach the dimmed page behind it', () => {
    render(<Harness startOpen />);
    const close = screen.getByTestId('focus-close');

    // Tabbing forward from the last focusable inside must wrap, not escape.
    close.focus();
    fireEvent.keyDown(document, { key: 'Tab' });

    expect(screen.getByTestId('outside')).not.toBe(document.activeElement);
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(true);
  });
});

describe('FocusButton', () => {
  it('is labelled for screen readers, not icon-only', () => {
    render(<FocusButton label="Preview" onClick={() => undefined} testId="focus-preview" />);

    expect(screen.getByTestId('focus-preview')).toHaveAccessibleName(/Preview/);
  });

  it('calls back when pressed', () => {
    const onClick = vi.fn();
    render(<FocusButton label="Preview" onClick={onClick} />);

    fireEvent.click(screen.getByRole('button'));

    expect(onClick).toHaveBeenCalledOnce();
  });
});
