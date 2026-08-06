import { TriangleAlert } from 'lucide-react';
import { Component, type ErrorInfo, type ReactNode } from 'react';

import { toActionableError } from '../lib/errors';
import { Button } from './ui';

interface Props {
  children: ReactNode;
  /** Optional custom fallback; receives the caught error + a reset handler. */
  fallback?: (error: unknown, reset: () => void) => ReactNode;
  /** Reset when this value changes (e.g. the route key), so navigating away clears a crash. */
  resetKey?: unknown;
}

interface State {
  error: unknown;
}

/**
 * Catches render-time crashes so a bug in one panel shows a branded, recoverable screen instead of
 * a blank white page (phase-48). This is the last line of the error-UX story: async failures are
 * handled inline with toasts (see lib/errors), but a thrown render error can only be caught here.
 * The taxonomy mapper gives even an opaque crash an actionable message.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: unknown): State {
    return { error };
  }

  componentDidUpdate(prev: Props): void {
    // A navigation (changed resetKey) clears a prior crash so the user isn't stuck.
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.reset();
    }
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error('Unhandled render error', error, info.componentStack);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    const actionable = toActionableError(error);
    return (
      <div
        role="alert"
        className="flex min-h-[60vh] flex-col items-center justify-center gap-4 p-8 text-center"
        data-testid="error-boundary"
      >
        {/* A stated icon, not a decorative blob: the one screen where a user needs to read what
            happened is the worst place to spend attention on ornament. */}
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-danger/10 text-danger ring-1 ring-inset ring-danger/20">
          <TriangleAlert aria-hidden className="h-5 w-5" strokeWidth={1.75} />
        </span>
        <div className="space-y-1">
          <h2 className="text-lg font-semibold text-fg">{actionable.title}</h2>
          <p className="max-w-md text-sm text-fg-muted">{actionable.description}</p>
          {actionable.hint ? (
            <p className="max-w-md text-xs text-fg-subtle">{actionable.hint}</p>
          ) : null}
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={this.reset} data-testid="error-boundary-retry">
            Try again
          </Button>
          <Button onClick={() => window.location.reload()} data-testid="error-boundary-reload">
            Reload
          </Button>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
