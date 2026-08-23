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

  it('turns a freeform description into an editable, unsaved draft', async () => {
    const onDraft = vi.fn().mockResolvedValue({
      app_name: 'Focus',
      features: [
        {
          name: 'Todos',
          description: 'manage a todo list',
          inputs: ['title'],
          expected_behaviors: ['the new todo appears'],
          acceptance_criteria: [{ text: 'Adding a todo shows it in the list', kind: 'e2e' }],
        },
        {
          name: 'Auth',
          description: '',
          inputs: [],
          expected_behaviors: [],
          acceptance_criteria: [{ text: 'A user can log in', kind: 'unit' }],
        },
      ],
    });
    const onSave = vi.fn();
    render(<RequirementsForm initialSpec={null} onSave={onSave} onDraft={onDraft} />);

    fireEvent.change(screen.getByTestId('draft-description'), {
      target: { value: 'a todo app with login' },
    });
    fireEvent.click(screen.getByTestId('draft-generate'));

    await waitFor(() => expect(onDraft).toHaveBeenCalledWith('a todo app with login'));

    // The draft lands as ordinary form rows: editable, removable, and not yet saved.
    await waitFor(() => expect(value('feature-name-0')).toBe('Todos'));
    expect(value('feature-name-1')).toBe('Auth');
    expect(value('criterion-text-0-0')).toBe('Adding a todo shows it in the list');
    expect(value('criterion-kind-0-0')).toBe('e2e');
    expect(value('feature-input-0-0')).toBe('title');
    expect(value('feature-behavior-0-0')).toBe('the new todo appears');
    expect(screen.getByTestId('remove-feature-1')).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled(); // nothing is saved until the user says so

    // …and the user can still edit and add to it before saving.
    fireEvent.change(screen.getByTestId('feature-name-1'), { target: { value: 'Accounts' } });
    fireEvent.click(screen.getByTestId('requirements-save'));

    expect(onSave).toHaveBeenCalledTimes(1);
    const spec = onSave.mock.calls[0][0] as RequirementSpecInput;
    expect(spec.features.map((f) => f.name)).toEqual(['Todos', 'Accounts']);
    expect(spec.features[0].acceptance_criteria[0].id).toBeNull(); // unsaved → no join key yet
  });

  it('keeps features the user already filled in when a draft arrives', async () => {
    const onDraft = vi.fn().mockResolvedValue({
      app_name: 'Focus',
      features: [
        {
          name: 'Todos',
          description: '',
          inputs: [],
          expected_behaviors: [],
          acceptance_criteria: [{ text: 'can add a todo', kind: 'either' }],
        },
      ],
    });
    render(<RequirementsForm initialSpec={null} onSave={vi.fn()} onDraft={onDraft} />);

    fireEvent.change(screen.getByTestId('feature-name-0'), { target: { value: 'Mine' } });
    fireEvent.change(screen.getByTestId('draft-description'), { target: { value: 'a todo app' } });
    fireEvent.click(screen.getByTestId('draft-generate'));

    await waitFor(() => expect(value('feature-name-1')).toBe('Todos'));
    expect(value('feature-name-0')).toBe('Mine'); // the user's own row is never overwritten
  });

  it('proposes an app name the user can edit, and saves it', async () => {
    const onDraft = vi.fn().mockResolvedValue({
      app_name: 'Focus',
      features: [
        {
          name: 'Todos',
          description: '',
          inputs: [],
          expected_behaviors: [],
          acceptance_criteria: [{ text: 'can add a todo', kind: 'either' }],
        },
      ],
    });
    const onSave = vi.fn();
    render(<RequirementsForm initialSpec={null} onSave={onSave} onDraft={onDraft} />);

    fireEvent.change(screen.getByTestId('draft-description'), { target: { value: 'a todo app' } });
    fireEvent.click(screen.getByTestId('draft-generate'));
    await waitFor(() => expect(value('app-name')).toBe('Focus'));

    // It is a proposal, not a decision: the user renames it and that is what is saved.
    fireEvent.change(screen.getByTestId('app-name'), { target: { value: 'Momentum' } });
    fireEvent.click(screen.getByTestId('requirements-save'));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    expect(onSave.mock.calls[0][0].app_name).toBe('Momentum');
  });

  it('never overwrites a name the user already typed', async () => {
    const onDraft = vi.fn().mockResolvedValue({
      app_name: 'Focus',
      features: [
        {
          name: 'Todos',
          description: '',
          inputs: [],
          expected_behaviors: [],
          acceptance_criteria: [{ text: 'can add a todo', kind: 'either' }],
        },
      ],
    });
    render(<RequirementsForm initialSpec={null} onSave={vi.fn()} onDraft={onDraft} />);

    fireEvent.change(screen.getByTestId('app-name'), { target: { value: 'Mine' } });
    fireEvent.change(screen.getByTestId('draft-description'), { target: { value: 'a todo app' } });
    fireEvent.click(screen.getByTestId('draft-generate'));

    await waitFor(() => expect(value('feature-name-0')).toBe('Todos'));
    expect(value('app-name')).toBe('Mine');
  });

  it('falls back to manual entry when the draft call fails', async () => {
    const failure = new Error('502');
    const onDraft = vi.fn().mockRejectedValue(failure);
    const onDraftError = vi.fn();
    render(
      <RequirementsForm
        initialSpec={null}
        onSave={vi.fn()}
        onDraft={onDraft}
        onDraftError={onDraftError}
      />,
    );

    fireEvent.change(screen.getByTestId('draft-description'), { target: { value: 'a todo app' } });
    fireEvent.click(screen.getByTestId('draft-generate'));

    await waitFor(() => expect(onDraftError).toHaveBeenCalled());
    expect(String(onDraftError.mock.calls[0][0])).toMatch(/manually/i);
    // The rejection rides along so the host can report the server's own words — an unconfigured
    // provider key says how to fix itself, where the generic fallback would bury it.
    expect(onDraftError.mock.calls[0][1]).toBe(failure);
    expect(screen.getByTestId('feature-name-0')).toBeInTheDocument(); // the form still works
  });

  it('offers no draft entry point when the assist is unavailable', () => {
    render(<RequirementsForm initialSpec={null} onSave={vi.fn()} />);
    expect(screen.queryByTestId('requirements-draft')).not.toBeInTheDocument();
    expect(screen.getByTestId('add-feature')).toBeInTheDocument(); // manual capture is untouched
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
