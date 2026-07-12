import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Modal } from './Modal';

describe('Modal', () => {
  it('renders content when open and closes on Escape', () => {
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Confirm">
        Body content
      </Modal>,
    );
    expect(screen.getByText('Body content')).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });

  it('renders nothing when closed', () => {
    render(
      <Modal open={false} onClose={vi.fn()} title="Confirm">
        Body content
      </Modal>,
    );
    expect(screen.queryByText('Body content')).not.toBeInTheDocument();
  });
});
