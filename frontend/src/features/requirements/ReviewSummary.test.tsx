import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { RequirementSpecDto } from '../../lib/types';
import { ReviewSummary } from './ReviewSummary';

const SPEC: RequirementSpecDto = {
  project_id: 'p1',
  version: 3,
  app_name: 'Focus',
  created_at: '2026-01-01T00:00:00Z',
  features: [
    {
      name: 'Todos',
      description: 'manage a todo list',
      inputs: ['title'],
      expected_behaviors: ['todo appears in the list'],
      acceptance_criteria: [
        { id: 'ac-1', text: 'A todo can be added', kind: 'unit' },
        { id: 'ac-2', text: 'Adding a todo shows it in the list', kind: 'e2e' },
      ],
    },
    {
      name: 'Auth',
      description: '',
      inputs: [],
      expected_behaviors: [],
      acceptance_criteria: [{ id: 'ac-3', text: 'A user can log in', kind: 'either' }],
    },
  ],
};

describe('ReviewSummary', () => {
  it('renders the whole spec: features, criteria, kinds and version', () => {
    render(<ReviewSummary spec={SPEC} onEdit={vi.fn()} />);

    expect(screen.getByTestId('review-version')).toHaveTextContent('3');
    expect(screen.getByText('Todos')).toBeInTheDocument();
    expect(screen.getByText('Auth')).toBeInTheDocument();
    expect(screen.getByText('A todo can be added')).toBeInTheDocument();
    expect(screen.getByText('Adding a todo shows it in the list')).toBeInTheDocument();
    expect(screen.getByText('A user can log in')).toBeInTheDocument();
    // Both features rendered as sections
    expect(screen.getByTestId('review-feature-0')).toBeInTheDocument();
    expect(screen.getByTestId('review-feature-1')).toBeInTheDocument();
  });

  it('invokes onEdit when the edit affordance is used', () => {
    const onEdit = vi.fn();
    render(<ReviewSummary spec={SPEC} onEdit={onEdit} />);

    fireEvent.click(screen.getByTestId('review-edit'));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });
});
