import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { RequirementSpecInput } from '../../lib/types';
import { RequirementsForm } from './RequirementsForm';

function value(testid: string): string {
  return (screen.getByTestId(testid) as HTMLInputElement).value;
}

describe('RequirementsForm', () => {
  it('builds a multi-feature spec via forms and saves it', () => {
    const onSave = vi.fn();
    render(<RequirementsForm initialSpec={null} onSave={onSave} />);

    // Feature 0
    fireEvent.change(screen.getByTestId('feature-name-0'), { target: { value: 'Todos' } });
    fireEvent.change(screen.getByTestId('criterion-text-0-0'), {
      target: { value: 'Adding a todo shows it in the list' },
    });

    // Add a second feature and fill it
    fireEvent.click(screen.getByTestId('add-feature'));
    fireEvent.change(screen.getByTestId('feature-name-1'), { target: { value: 'Auth' } });
    fireEvent.change(screen.getByTestId('criterion-text-1-0'), {
      target: { value: 'A user can log in' },
    });
    fireEvent.change(screen.getByTestId('criterion-kind-1-0'), { target: { value: 'e2e' } });

    fireEvent.click(screen.getByTestId('requirements-save'));

    expect(onSave).toHaveBeenCalledTimes(1);
    const spec = onSave.mock.calls[0][0] as RequirementSpecInput;
    expect(spec.features.map((f) => f.name)).toEqual(['Todos', 'Auth']);
    expect(spec.features[0].acceptance_criteria[0]).toMatchObject({
      text: 'Adding a todo shows it in the list',
      kind: 'either',
    });
    expect(spec.features[1].acceptance_criteria[0]).toMatchObject({
      text: 'A user can log in',
      kind: 'e2e',
    });
  });

  it('blocks save and shows an inline error for a blank criterion', () => {
    const onSave = vi.fn();
    render(<RequirementsForm initialSpec={null} onSave={onSave} />);

    fireEvent.change(screen.getByTestId('feature-name-0'), { target: { value: 'Todos' } });
    // Leave criterion text blank
    fireEvent.click(screen.getByTestId('requirements-save'));

    expect(screen.getByTestId('criterion-text-error-0-0')).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('blocks save and shows an inline error for a missing feature name', () => {
    const onSave = vi.fn();
    render(<RequirementsForm initialSpec={null} onSave={onSave} />);

    fireEvent.change(screen.getByTestId('criterion-text-0-0'), { target: { value: 'something' } });
    fireEvent.click(screen.getByTestId('requirements-save'));

    expect(screen.getByTestId('feature-name-error-0')).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it('appends AI proposals as editable criteria without overwriting user input', async () => {
    const onSuggest = vi
      .fn()
      .mockResolvedValue([{ text: 'Deleting a todo removes it', kind: 'unit' }]);
    render(<RequirementsForm initialSpec={null} onSave={vi.fn()} onSuggest={onSuggest} />);

    fireEvent.change(screen.getByTestId('feature-name-0'), { target: { value: 'Todos' } });
    fireEvent.change(screen.getByTestId('criterion-text-0-0'), {
      target: { value: 'A todo can be added' },
    });

    fireEvent.click(screen.getByTestId('suggest-criteria-0'));

    await waitFor(() => expect(onSuggest).toHaveBeenCalledWith('Todos', ''));
    // The manual row is preserved; the proposal is appended as a new, editable row.
    await waitFor(() => expect(value('criterion-text-0-1')).toBe('Deleting a todo removes it'));
    expect(value('criterion-text-0-0')).toBe('A todo can be added');
    expect(value('criterion-kind-0-1')).toBe('unit');
  });

  it('seeds fields from an existing spec for editing', () => {
    const initial: RequirementSpecInput = {
      features: [
        {
          name: 'Todos',
          description: 'manage todos',
          inputs: ['title'],
          expected_behaviors: ['appears in list'],
          acceptance_criteria: [{ id: 'ac-1', text: 'can add a todo', kind: 'unit' }],
        },
      ],
    };
    render(<RequirementsForm initialSpec={initial} onSave={vi.fn()} />);

    expect(value('feature-name-0')).toBe('Todos');
    expect(value('criterion-text-0-0')).toBe('can add a todo');
    expect(value('feature-input-0-0')).toBe('title');
  });
});
