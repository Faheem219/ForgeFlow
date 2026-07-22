import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SensitiveField } from './SensitiveField';

describe('SensitiveField', () => {
  it('shows "stored" when set — and never renders the value', () => {
    render(<SensitiveField isSet readOnly note="managed in the vault" />);

    expect(screen.getByTestId('sensitive-status')).toHaveTextContent('••••••');
    expect(screen.getByText('managed in the vault')).toBeInTheDocument();
    // No input in read-only mode, and no plaintext anywhere.
    expect(screen.queryByTestId('sensitive-input')).not.toBeInTheDocument();
  });

  it('shows "not set" when there is no secret', () => {
    render(<SensitiveField isSet={false} readOnly />);
    expect(screen.getByTestId('sensitive-status')).toHaveTextContent('not set');
  });

  it('is write-only: the editor is a password input that starts empty', () => {
    render(<SensitiveField isSet onSubmit={vi.fn()} />);

    fireEvent.click(screen.getByTestId('sensitive-edit'));
    const input = screen.getByTestId('sensitive-input') as HTMLInputElement;
    // A password type never displays characters, and it does not carry the existing secret.
    expect(input.type).toBe('password');
    expect(input.value).toBe('');
  });

  it('emits only the newly-typed secret, then clears it from the DOM', () => {
    const onSubmit = vi.fn();
    render(<SensitiveField isSet={false} onSubmit={onSubmit} />);

    fireEvent.click(screen.getByTestId('sensitive-edit'));
    fireEvent.change(screen.getByTestId('sensitive-input'), { target: { value: 's3cret' } });
    fireEvent.click(screen.getByTestId('sensitive-save'));

    expect(onSubmit).toHaveBeenCalledWith('s3cret');
    // After submit the input is gone (back to status view) — the value isn't left rendered.
    expect(screen.queryByTestId('sensitive-input')).not.toBeInTheDocument();
    expect(screen.queryByText('s3cret')).not.toBeInTheDocument();
  });
});
