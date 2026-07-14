import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { Stage, StageStatus } from '../../lib/types';
import { StageNavigator } from './StageNavigator';

function renderNav(
  statusByStage: Partial<Record<Stage, StageStatus>>,
  overrides: Partial<Parameters<typeof StageNavigator>[0]> = {},
) {
  const onSelect = vi.fn();
  const onAction = vi.fn();
  render(
    <StageNavigator
      statusByStage={statusByStage}
      activeStage="design"
      onSelect={onSelect}
      onAction={onAction}
      {...overrides}
    />,
  );
  return { onSelect, onAction };
}

describe('StageNavigator', () => {
  it('renders all six stages with their status labels', () => {
    renderNav({ design: 'complete', build: 'in_progress' });

    for (const label of ['Design', 'Requirements', 'Build', 'Test', 'Deploy', 'Validate']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText('Complete')).toBeInTheDocument();
    expect(screen.getByText('In progress')).toBeInTheDocument();
  });

  it('lets the user click any stage (enter-anywhere)', () => {
    const { onSelect } = renderNav({});
    fireEvent.click(screen.getByTestId('stage-build'));
    expect(onSelect).toHaveBeenCalledWith('build');
  });

  it('shows lock hints on deploy/validate until their prerequisites are complete', () => {
    renderNav({});
    // Two locked stages → two lock hints.
    expect(screen.getAllByText(/Locked until/)).toHaveLength(2);
    expect(screen.getByText('Locked until Build is complete')).toBeInTheDocument();
    expect(screen.getByText('Locked until Deploy is complete')).toBeInTheDocument();
  });

  it('drops the deploy lock hint once build is complete', () => {
    renderNav({ build: 'complete' });
    expect(screen.queryByText('Locked until Build is complete')).not.toBeInTheDocument();
    // validate is still locked (deploy not complete).
    expect(screen.getByText('Locked until Deploy is complete')).toBeInTheDocument();
  });

  it('exposes refine/skip/proceed in the per-stage menu and reports the chosen action', () => {
    const { onAction } = renderNav({});
    fireEvent.click(screen.getByTestId('stage-menu-design'));

    expect(screen.getByTestId('stage-action-design-refine')).toBeInTheDocument();
    expect(screen.getByTestId('stage-action-design-skip')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('stage-action-design-proceed'));
    expect(onAction).toHaveBeenCalledWith('design', 'proceed');
  });
});
