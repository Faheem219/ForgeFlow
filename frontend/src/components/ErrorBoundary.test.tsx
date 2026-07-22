import { fireEvent, render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '../lib/apiClient';
import { ErrorBoundary } from './ErrorBoundary';

// A component that throws on demand, so we can exercise the boundary deterministically.
function Boom({ error }: { error: unknown }): ReactNode {
  throw error;
}

// React logs caught render errors to console.error — silence it so the suite output stays clean.
let consoleError: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => {
  consoleError.mockRestore();
});

describe('ErrorBoundary', () => {
  it('renders children when nothing throws', () => {
    render(
      <ErrorBoundary>
        <div>all good</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText('all good')).toBeInTheDocument();
    expect(screen.queryByTestId('error-boundary')).not.toBeInTheDocument();
  });

  it('shows a branded, actionable fallback when a child crashes', () => {
    render(
      <ErrorBoundary>
        <Boom error={new Error('Cannot read properties of undefined')} />
      </ErrorBoundary>,
    );

    expect(screen.getByTestId('error-boundary')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    // The taxonomy mapper surfaces the underlying message.
    expect(screen.getByText('Cannot read properties of undefined')).toBeInTheDocument();
    expect(screen.getByTestId('error-boundary-retry')).toBeInTheDocument();
    expect(screen.getByTestId('error-boundary-reload')).toBeInTheDocument();
  });

  it('maps a thrown ProviderError to its actionable message + hint', () => {
    const provider = new ApiError(
      502,
      "Stitch's quota is exhausted",
      'provider_error',
      undefined,
      'Switch to figma in settings.',
    );
    render(
      <ErrorBoundary>
        <Boom error={provider} />
      </ErrorBoundary>,
    );

    expect(screen.getByText('A provider is unavailable')).toBeInTheDocument();
    expect(screen.getByText("Stitch's quota is exhausted")).toBeInTheDocument();
    expect(screen.getByText('Switch to figma in settings.')).toBeInTheDocument();
  });

  it('recovers when "Try again" is clicked and the child no longer throws', () => {
    let shouldThrow = true;
    function Flaky(): ReactNode {
      if (shouldThrow) throw new Error('transient');
      return <div>recovered</div>;
    }

    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId('error-boundary')).toBeInTheDocument();

    shouldThrow = false;
    fireEvent.click(screen.getByTestId('error-boundary-retry'));

    expect(screen.getByText('recovered')).toBeInTheDocument();
    expect(screen.queryByTestId('error-boundary')).not.toBeInTheDocument();
  });

  it('clears a crash automatically when the resetKey changes', () => {
    const { rerender } = render(
      <ErrorBoundary resetKey="design">
        <Boom error={new Error('crash')} />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId('error-boundary')).toBeInTheDocument();

    // Navigating to another stage changes the key → the boundary resets and renders the new child.
    rerender(
      <ErrorBoundary resetKey="build">
        <div>build panel</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText('build panel')).toBeInTheDocument();
    expect(screen.queryByTestId('error-boundary')).not.toBeInTheDocument();
  });

  it('supports a custom fallback render', () => {
    render(
      <ErrorBoundary fallback={(err) => <div>custom: {(err as Error).message}</div>}>
        <Boom error={new Error('specific')} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('custom: specific')).toBeInTheDocument();
  });
});
